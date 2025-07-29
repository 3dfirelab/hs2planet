
import os 
import warnings 
import shutil
import pyproj
from datetime import datetime, timezone
import pandas as pd 
import matplotlib.pyplot as plt 
import requests
import planete_api
import pdb 
import json 
import numpy as np 
from shapely.geometry import Point, mapping
from shapely.ops import transform
from pyproj import CRS
import math 
import sys
import glob 
import geopandas as gpd
from shapely.geometry import box
from shapely.errors import TopologicalError
import subprocess 
import time


def lonlat_to_utm_crs(lon, lat):
    zone = math.floor((lon + 180) / 6) + 1
    south = lat < 0
    epsg_code = 32700 + zone if south else 32600 + zone
    return CRS.from_epsg(epsg_code)

def extract_datetime_from_filename(f):
    basename = os.path.basename(f)
    date_str = basename.replace(f"LSA-509_MTG_MTFRPPIXEL-ListProduct_MTG-FD_", "").replace(".csv", "")
    return datetime.strptime(date_str, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)



if __name__ == '__main__':

    
    dir_hs = f"{os.environ['HOME']}/AERIS/FCI/hotspots/"
    domain =  [-10,35,20,52] # lonmin,latmin, lonmax, latmax
    bbox = box(*domain)  # box(minx, miny, maxx, maxy)
    radius_km = 0.6
    time_update = 300. # in second
    srcDir = os.path.dirname(__file__)
    subprocess.run([f"{srcDir}/mount_aeris.sh"], check=True)

    ip_server_planete =  'safire.atmosphere.aero'
    mission_id = 'SILEX'
    user_name = os.environ['planete_username']
    password  = os.environ['planete_username_passwd']
    
    # Define projection for accurate buffering (meters)
    proj_wgs84 = pyproj.CRS('EPSG:4326')

    #define planet connection
    token = planete_api.get_token(ip_server_planete, mission_id,user_name,password)
    geomarker_id = []
    geomarker_time = []
    
    last_time = datetime.now(timezone.utc)

    try:
        ii = 0 
        while( True ):
            #get lastest hs file
            files_sorted = sorted(glob.glob(os.path.join(dir_hs, "*.csv")))
            latest_file = files_sorted[-1] if files_sorted else None
            if latest_file:
                latest_datetime = extract_datetime_from_filename(latest_file)
                print("Latest hs file used:", os.path.basename(latest_file), end = '')
                sys.stdout.flush()
                flag_hs = 'found' 
            else:
               flag_hs = 'nodata' 
           
            deltatime_ref = 590 # 20 min
            
            if flag_hs == 'found':
                if ii > 0:
                    deltatime = (latest_datetime - last_time).total_seconds()
                else:
                    deltatime = 1.e6
    
            else:
                continue
            
            #print(deltatime)
            
            if (deltatime > deltatime_ref) | (ii==0):
                #update hs data
                hs_data = pd.read_csv(latest_file, delimiter=',', header=0)
                last_time = latest_datetime
            else:
                print(' ... waiting ')
                time.sleep(time_update)
                subprocess.run([f"{srcDir}/mount_aeris.sh"], check=True)
                continue
            
            # Create Point geometries
            geometry = [Point(xy) for xy in zip(hs_data["LONGITUDE"], hs_data["LATITUDE"])]

            # Convert to GeoDataFrame
            gdf = gpd.GeoDataFrame(hs_data, geometry=geometry, crs="EPSG:4326")

            # Clip the GeoDataFrame
            gdf_clipped = gpd.clip(gdf, bbox)
          
            #print(f"****** found {len(gdf_clipped)} hs in SILEX domain and send to planet")
            for idx, row in gdf_clipped.iterrows():
                try:
                    lon, lat = row.geometry.x, row.geometry.y
                    point = Point(lon, lat)
                    
                    proj_utm = lonlat_to_utm_crs(lon, lat)

                    # Projection functions
                    project = pyproj.Transformer.from_crs(proj_wgs84, proj_utm, always_xy=True).transform
                    project_back = pyproj.Transformer.from_crs(proj_utm, proj_wgs84, always_xy=True).transform

                    # Project to meters
                    point_proj = transform(project, point)

                    # Create buffer in meters
                    circle_proj = point_proj.buffer(radius_km * 1000)
                    circle_wgs84 = transform(project_back, circle_proj)

                    # Create GeoJSON Feature
                    feature = {
                        "type": "Feature",
                        "geometry": mapping(circle_wgs84),
                        "properties": {
                            "group": "hotspot",
                            "color": "#ff0000",
                            "time UTC": latest_datetime.strftime('%Y-%m-%d %H:%M'),
                        }
                    }

                    circle_data_json = {
                            "type":"Feature",
                            "geometry":mapping(point),
                            "properties":{
                                "radius":radius_km,
                                "FRP (MW)": row['FRP'],
                                "group":"hotspots MTG",
                                "color":"#ff0000",
                                "time UTC": latest_datetime.strftime('%Y-%m-%d %H:%M'),
                                }
                            }
                    
                    wrapped_feature = {"feature": circle_data_json}
                    time_hs = time.time()

                    # Add to Planete API
                    marker_id = planete_api.add_geomarker(ip_server_planete, mission_id, token, wrapped_feature)
                    geomarker_id.append(marker_id)
                    geomarker_time.append(latest_datetime)

                except TopologicalError as e:
                    print('')
                    print(f"Geometry error at index {idx}: {e}")
                except Exception as e:
                    print('')
                    print(f"Unexpected error at index {idx}: {e}")
          
            ii += 1 

            id_to_remove = np.where(np.array(geomarker_time) < geomarker_time[-1])[0] #keep last 10min
            print(f" rm {len(id_to_remove)} hs -- ", end='')
            if len(id_to_remove)>0:
                for ii in id_to_remove:
                    planete_api.delete_geomarker(ip_server_planete, mission_id, token, geomarker_id[ii])
                    #del geomarker_id[ii]
                    #del geomarker_time[ii]
            
            print(f" {len(gdf_clipped)} hs" + u'   \u2714')
            sys.stdout.flush()
            
            time.sleep(time_update)
            subprocess.run([f"{srcDir}/mount_aeris.sh"], check=True)

    except KeyboardInterrupt:
        for idx in geomarker_id:
            planete_api.delete_geomarker(ip_server_planete, mission_id, token, idx)
