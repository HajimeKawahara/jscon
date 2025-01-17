import numpy as np
import matplotlib.pyplot as plt
from astropy.wcs import WCS
import pandas as pd
import matplotlib.pyplot as plt
from astropy.coordinates import SkyCoord  # High-level coordinates
from astropy.coordinates import ICRS, Galactic, FK4, FK5  # Low-level frames
from astropy.coordinates import Angle, Latitude, Longitude  # Angles
import numpy as np
import astropy.units as u
import scipy.integrate as integrate
from scipy import interpolate
import importlib
from multiprocessing import Process
import os
from jscon import make_image as mk
from jscon import epsf_fit
from jscon import astrometry
from jscon import parameter_load
import argparse


arc_rad = 4.84814e-6


def main_pool(i_core, time_arr, time_ref, df_astro, pixel_targets, hwmag_targets, gauss_conv,  mask, rand_nums,  ext_size, xmax, ymax, displace_amp = 3, out_folder ="./out/",  n_oversample = 2,  n_maxiter=5, pix_arcsec = 0.4):
    dx_arr = []
    dy_arr = []
    ra = df_astro["ra"]
    dec = df_astro["dec"]
    pm_ra = df_astro["pm_ra"]
    pm_dec = df_astro["pm_dec"]    
    distance = df_astro["distance"]    

    for i_ter in range(len(time_arr)):
        ra_delta, dec_delta = astrometry.compute_delta_ra_dec_one_phase(ra, dec, pm_ra, pm_dec, distance, time_arr[i_ter], time_ref)             
        ra_delta_pix = 0.001 * ra_delta/pix_arcsec
        dec_delta_pix = 0.001 * dec_delta/pix_arcsec
        displace = 2 * displace_amp * rand_nums[i_core, i_ter] - displace_amp
        pixel_targets_mov = np.array([pixel_targets[0] + displace [0] + ra_delta_pix , pixel_targets[1] + displace[1] + dec_delta_pix])
        pixel_targets_without_mov = np.array([pixel_targets[0] + displace [0], pixel_targets[1] + displace[1]])
        image, mask_included = mk.make_image(pixel_targets_mov, hwmag_targets, gauss_conv, xmax, ymax)
        x_target = pixel_targets_without_mov[0][mask]
        y_target = pixel_targets_without_mov[1][mask]
        dx, dy, epsf_image  = epsf_fit.main(image, x_target, y_target, ext_size = ext_size, n_oversample = n_oversample, n_maxiter=n_maxiter )
        dx_arr.append(dx)
        dy_arr.append(dy)
        out_file = os.path.join( out_folder , "displace_%.5f" % (time_arr[i_ter]))
        np.savez(out_file, dx = dx, dy = dy, displace= displace, epsf_image = epsf_image)

    return dx_arr, dy_arr


def pre_main(pixel_targets, hwmag_targets, gauss_conv, mask, ext_size, xmax, ymax, displace_amp = 3, n_iter =2, out_folder ="./out/", n_oversample = 2, n_maxiter=5):

    displace = 2 * displace_amp * np.random.rand(2) - displace_amp
    pixel_targets_mov = np.array([pixel_targets[0] + displace [0], pixel_targets[1] + displace[1]])
    image, mask_included = mk.make_image(pixel_targets_mov, hwmag_targets, gauss_conv,  xmax, ymax)
    x_target = pixel_targets_mov [0][mask * mask_included]
    y_target = pixel_targets_mov [1][mask * mask_included]
    dx, dy, epsf_image  = epsf_fit.main(image, x_target, y_target, ext_size = ext_size, n_oversample =  n_oversample , n_maxiter=n_maxiter)
    dr = (dx**2 + dy**2)**0.5
    mask_bad =  dr  < 0.05
    mask_new = np.copy(mask * mask_included)
    mask_new[mask* mask_included] *= mask_bad
    return dx, dy, mask_new

def main(n_core, diameter, lambda_now, sigma_ace, pix_arcsec, time_ref, time_start, time_end, time_sample_num, \
    sky_dir_l, sky_dir_b, xmax, ymax, ext_size, file_catalog):


    ## output folder
    out_folder = dic_params["out_folder"]

    if not os.path.exists(out_folder):
        os.makedirs(out_folder)

    sigma_psf_arcsec_val = (0.512/1.177410) *(lambda_now/diameter )/arc_rad
    sigma_tot =np.sqrt( sigma_psf_arcsec_val**2 + sigma_ace**2)
    sigma_pix = sigma_tot/pix_arcsec #unit of pix
    time_arr = np.linspace(time_start, time_end, int(time_sample_num))

    ## Load catalog info
    df = mk.get_catalog_info(file_catalog)
    mask_hwmag = df["hwmag"] < 14.5
    hwmag_targets = df["hwmag"] 
    c = SkyCoord(df["ra"].values, df["dec"].values, frame="icrs", unit="deg")
    gal_l = c.galactic.l.deg
    gal_b = c.galactic.b.deg
    ra = df["ra"].values * np.pi/180.0
    dec = df["dec"].values * np.pi/180.0

    ## Make convolved gaussian for ePSF (PSF & PRF)
    x_cen_arr = np.linspace(-100, 100, 20000)
    y_val_arr = []
    for x_cen in x_cen_arr:
        val = mk.pixel_1d_gaussian(x_cen, .5, sigma_pix)
        y_val_arr.append(val)
    gauss_conv = interpolate.interp1d(x_cen_arr,  y_val_arr)

    ## Make WCS & pixel values for targets
    pixel_targets = mk.make_pixel_coordinates(gal_l, gal_b, sky_dir_l, sky_dir_b, pix_arcsec)
    dx, dy, mask_new = pre_main(pixel_targets, hwmag_targets, gauss_conv, mask_hwmag, ext_size, xmax, ymax)
    
    ### Make astronometric info
    pm_ra, pm_dec, distance = astrometry.make_proper_motions_distance(len(gal_l))
    np.savez(os.path.join(out_folder, "input_astro_info"), pixel_targets = pixel_targets, distance = distance, pm_ra = pm_ra, pm_dec = pm_dec, time = time_arr, time_ref = time_ref, ra = ra, dec = dec,  mask_targets =  mask_new)
    df_astro = astrometry.make_astrometry(ra, dec, pm_ra, pm_dec, distance)

    ## Run main function
    process_list = []
    time_split = np.array_split(time_arr,n_core)

    rand_nums = np.random.rand(n_core, len(time_arr), 2 )
    for i in range(n_core):
        process = Process(
            target=main_pool,
            kwargs={"i_core":i, "df_astro":df_astro, "time_ref":time_ref, "time_arr":time_split[i], 'pixel_targets': pixel_targets, "hwmag_targets":hwmag_targets, \
                    "gauss_conv": gauss_conv, "ext_size":ext_size, "xmax": xmax, "ymax": ymax, "mask": mask_new,"out_folder":out_folder, "rand_nums":rand_nums, "pix_arcsec":pix_arcsec})
        process.start()
        process_list.append(process)

    for process in process_list:
        process.join()



if __name__=="__main__":

    parser =argparse.ArgumentParser()
    parser.add_argument('--par', default='')
    args=parser.parse_args()


    ## Input
    dic_params = parameter_load.load_params(args.par)
    n_core = int(dic_params["n_core"])
    diameter = dic_params["diameter"] 
    lambda_now = dic_params["lambda_now"]* 10**-6
    sigma_ace = dic_params["sigma_ace"]
    pix_arcsec = dic_params["pix_arcsec"]                  
    time_ref = dic_params["time_ref"]   
    time_start = dic_params["time_start"]  
    time_end = dic_params["time_end"]  
    time_sample_num = dic_params["time_sample_num"]  
    sky_dir_l = dic_params["sky_dir_l"]
    sky_dir_b = dic_params["sky_dir_b"]  
    xmax = int(dic_params["xmax"] )
    ymax = int(dic_params["ymax"] )
    file_catalog = str(dic_params["catalog"])
    ext_size = int(dic_params["ext_size"])

    ## Main
    main(n_core, diameter, lambda_now, sigma_ace, pix_arcsec, time_ref, time_start, time_end, time_sample_num, \
    sky_dir_l, sky_dir_b, xmax, ymax, ext_size, file_catalog)


