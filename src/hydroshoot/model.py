# -*- coding: utf-8 -*-
"""
Created on Mon Feb  6 18:23:26 2017

@author: albashar
"""

# -*- coding: utf-8 -*-
"""
Created on Tue Dec 13 13:07:33 2016

@author: albashar
"""
import datetime as dt
import os.path
from pandas import read_csv, DataFrame, date_range, DatetimeIndex #, read_excel
from copy import deepcopy
import numpy as np
import time

import openalea.mtg.traversal as traversal
from openalea.plantgl.all import Scene, surface


import hydroshoot.architecture as HSArc
import hydroshoot.irradiance as HSCaribu
import hydroshoot.exchange as HSExchange
import hydroshoot.hydraulic as HSHyd
import hydroshoot.energy as HSEnergy
import hydroshoot.display as HSVisu


def run(g, wd, sdate, edate, emdate, scene, **kwargs):
    """
    Calculates leaf gas and energy exchange in addition to the hydraulic structure of an individual plant.

    :Parameters:
    - **g**: a multiscale tree graph object
    - **wd**: string, working directory
    - **sdate**: datetime, start date of the simulation period
    - **edate**: datetime, end date of the simulation period
    - **emdate**: datetime, emeergence date (for the calculation of the cumulative degree-day temperature)
    - **scene**: PlantGl scene
    - **kwargs** can include:
        - **collar_label**
        - **E_type**
        - **E_type2**
        - **elevation**
        - **energy_budget**
        - **hydraulic_structure**
        - **icosphere_level**
        - **Kx_dict**
        - **latitude**
        - **leaf_lbl_prefix**
        - **limit**
        - **longitude**
        - **max_iter**
        - **MassConv**
        - **Na_dict**
        - **negligible_shoot_resistance**
        - **opt_prop**
        - **output_name**
        - **par_gs**
        - **par_K_vul**
        - **par_photo**
        - **par_photo_N**
        - **psi_error_threshold**
        - **psi_min**
        - **psi_soil**
        - **psi_step**
        - **rbt**
        - **rhyzo_solution**
        - **rhyzo_number**
        - **rhyzo_radii**
        - **roots**
        - **scene_rotation**
        - **simplified_form_factors**
        - **soil_class**: one of ('Sand','Loamy_Sand','Sandy_Loam','Loam', 'Silt','Silty_Loam','Sandy_Clay_Loam','Clay_Loam','Silty_Clay_Loam','Sandy_Clay','Silty_Clay','Clay')
        - **soil_dimensions**
        - **soil_water_deficit**
        - **solo**
        - **stem_lbl_prefix**
        - **sun2scene**
        - **t_base**
        - **t_cloud**
        - **t_error_crit**
        - **t_sky**
        - **tzone**
        - **turtle_format**
        - **turtle_sectors**
        - **unit_scene_length**

    TODO: replace by a class.
    """
    print '++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++'
    print '+ Project: ', wd.split('/')[-3:-1],'+'
    print '++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++'
    total_time_ON = time.time()
    output_name = 'results' if 'output_name' not in kwargs else kwargs['output_name']
#==============================================================================
# Initialisation
#==============================================================================
#   Climate data
    meteo_path = wd + 'meteo.input'
    meteo_tab = read_csv(meteo_path, sep=';', decimal='.', header=0)
    meteo_tab.time = DatetimeIndex(meteo_tab.time)
    meteo_tab = meteo_tab.set_index(meteo_tab.time)
    
#   Adding missing data
    if 'Ca' not in meteo_tab.columns:
        meteo_tab['Ca'] = [400.]*len(meteo_tab) # ppm [CO2]
    if 'Pa' not in meteo_tab.columns:
        meteo_tab['Pa'] = [101.3]*len(meteo_tab)# atmospheric pressure
    
    
    #   Determination of the simulation period
    datet = date_range(sdate, edate, freq='H')
    meteo = meteo_tab.ix[datet]

    # Reading available pre-dawn soil water potential data
    if 'psi_soil' in kwargs:
        psi_pd = DataFrame([kwargs['psi_soil']]*len(meteo.time),
                           index=meteo.time, columns=['psi'])
    else:
        assert (os.path.isfile(wd+'psi_soil.input')), "The 'psi_soil.input' file is missing."
        psi_pd = read_csv(wd+'psi_soil.input',sep=';',decimal='.').set_index('time')
        psi_pd.index = [dt.datetime.strptime(s, "%Y-%m-%d") for s in psi_pd.index]
    
    soil_water_deficit = True if not 'soil_water_deficit' in kwargs else kwargs['soil_water_deficit']

#   Unit length conversion (from scene unit to the standard [m]) unit)
    unit_scene_length = 'cm' if 'unit_scene_length' not in kwargs else kwargs['unit_scene_length']
    LengthConv = {'mm':1.e-3, 'cm':1.e-2, 'm':1.}[unit_scene_length]
#    LengthConv = 1.e-2 if 'LengthConv' not in kwargs else kwargs['LengthConv']


#   Determination of cumulative degree-days parameter
    t_base = 10 if 't_base' not in kwargs else kwargs['t_base']
    if 'tt' in kwargs:
        tt = kwargs['tt']
    elif min(meteo_tab.index) <= emdate:
        tdays = date_range(emdate, sdate, freq='D')
        tmeteo = meteo_tab.ix[tdays].Tac.to_frame()
        tmeteo = tmeteo.set_index(DatetimeIndex(tmeteo.index).normalize())
        df_min = tmeteo.groupby(tmeteo.index).aggregate(np.min).Tac
        df_max = tmeteo.groupby(tmeteo.index).aggregate(np.max).Tac
        df_tt = 0.5*(df_min + df_max) - t_base
        tt = df_tt.cumsum()[-1]
    else:
        raise ValueError ('Cumulative degree-days temperature is not provided.')

    print 'Cumulative degree-day temperature = %d °C'%tt


# Determination of pernial structure arms (for grapevine)
    arm_vid={g.node(vid).label:g.node(vid).components()[0]._vid \
    for vid in g.VtxList(Scale=2) if g.node(vid).label.startswith('arm') }

    # Soil reservoir dimensions (inter row, intra row, depth) [m]
    soil_dimensions = (3.6, 1.0, 1.2) if 'soil_dimensions' not in kwargs else kwargs['soil_dimensions']

#   Counter clockwise angle between the default X-axis direction (South) and the    
#    real direction of X-axis.
    scene_rotation = 0 if 'scene_rotation' not in kwargs else kwargs['scene_rotation']

#   sky and cloud temperature [degreeC]
    t_sky = -20. if 't_sky' not in kwargs else kwargs['t_sky']
    t_cloud = 2. if 't_cloud' not in kwargs else kwargs['t_cloud']
        

#   Topological location
    latitude = 43.61 if 'latitude' not in kwargs else kwargs['latitude']
    longitude = 3.87  if 'longitude' not in kwargs else kwargs['longitude']
    elevation = 44.0 if 'elevation' not in kwargs else kwargs['elevation']
    geo_location = (latitude, longitude, elevation)

#   Maximum number of iterations for both temperature and hydraulic calculations
    max_iter = 100 if 'max_iter' not in kwargs else kwargs['max_iter']

#   Maximum acceptable error threshold in hydraulic calculations
    psi_error_threshold = 0.05 if 'psi_error_threshold' not in kwargs else kwargs['psi_error_threshold']
    psi_step = 0.1 if not 'psi_step' in kwargs else kwargs['psi_step']
    
#   Maximum acceptable error threshold in temperature calculations
    t_error_crit = 0.02 if 't_error_crit' not in kwargs else kwargs['t_error_crit']
    
#   Pattern
    inter_dist, intra_dist, depth = soil_dimensions[:3]
    ymax, xmax = map(lambda x: x/ LengthConv, soil_dimensions[:2])
    pattern = ((-xmax/2.,-ymax/2.),(xmax/2.,ymax/2.))

#   Label prefix of the collar internode
    vtx_label = 'inT' if 'collar_label' not in kwargs else kwargs['collar_label']

#   Label prefix of the leaves
    leaf_lbl_prefix = 'L' if 'leaf_lbl_prefix' not in kwargs else kwargs['leaf_lbl_prefix']

#   Label prefices of stem elements
    stem_lbl_prefix=('in', 'Pet', 'cx') if 'stem_lbl_prefix' not in kwargs else kwargs['stem_lbl_prefix']
    
    E_type='Rg_Watt/m2' if 'E_type' not in kwargs else kwargs['E_type']
    E_type2 = 'Ei' if 'E_type2' not in kwargs else kwargs['E_type2']
    rbt = 2./3. if 'rbt' not in kwargs else kwargs['rbt']
#    ca = 360. if 'ca' not in kwargs else kwargs['ca']
    tzone='Europe/Paris' if 'tzone' not in kwargs else kwargs['tzone']
    turtle_sectors='46' if 'turtle_sectors' not in kwargs else kwargs['turtle_sectors']
    icosphere_level = None if 'icosphere_level' not in kwargs else kwargs['icosphere_level']
    
    turtle_format='soc' if 'turtle_format' not in kwargs else kwargs['turtle_format']
    
    MassConv = 18.01528 if 'MassConv' not in kwargs else kwargs['MassConv']
    limit=-0.000000001 if 'limit' not in kwargs else kwargs['limit']

    energy_budget = True if not 'energy_budget' in kwargs else kwargs['energy_budget']
    print 'Energy_budget: %s'%energy_budget
    if energy_budget:
        solo = True if 'solo' not in kwargs else kwargs['solo']
        simplified_form_factors = True if 'simplified_form_factors' not in kwargs else kwargs['simplified_form_factors']
    
#   Optical properties
    if 'opt_prop' not in kwargs:
        opt_prop={'SW':{'leaf':(0.06,0.07),'stem':(0.13,),'other':(0.06,0.07)},
                'LW':{'leaf':(0.04,0.07),'stem':(0.13,),'other':(0.06,0.07)}}
    else:
        opt_prop = kwargs['opt_prop']
    
#   Farquhar parameters
    if 'par_photo' not in kwargs:
        par_photo = HSExchange.par_photo_default()
    else:
        par_photo = kwargs['par_photo']

#   Stomatal conductance parameters
    hydraulic_structure = True if not 'hydraulic_structure' in kwargs else kwargs['hydraulic_structure']
    print 'Hydraulic structure: %s'%hydraulic_structure
    if hydraulic_structure:
        if 'par_gs' not in kwargs:
            par_gs = {'model':'misson', 'g0':0.0, 'm0':5.278,
                       'psi0':-1.,'D0':30.,'n':4.}
        else:
            par_gs = kwargs['par_gs']

        negligible_shoot_resistance = False if not 'negligible_shoot_resistance' in kwargs else kwargs['negligible_shoot_resistance']
    else:
        par_gs = {'model':'vpd', 'g0':0.0, 'm0':5.278,
                       'psi0':None,'D0':None,'n':None}

#   Parameters of maximum stem conductivity allometric relationship
    if 'Kx_dict' not in kwargs:
        Kx_dict = {'a':1.6,'b':2., 'min_kmax':0.}
    else:
        Kx_dict = kwargs['Kx_dict']

    psi_min = -3.0 if 'psi_min' not in kwargs else kwargs['psi_min']
    negligible_shoot_resistance = False if not 'negligible_shoot_resistance' in kwargs else kwargs['negligible_shoot_resistance']

#   Parameters of stem water conductivty's vulnerability to cavitation
    if 'par_K_vul' not in kwargs:
        par_K_vul = {'model': 'misson', 'fifty_cent': -0.51, 'sig_slope': 1.}
    else:
        par_K_vul = kwargs['par_K_vul']

#   Parameters of leaf Nitrogen content-related models
    if 'Na_dict' in kwargs:
        aN, bN, aM, bM = [kwargs['Na_dict'][ikey] for ikey in ('aN','bN','aM','bM')]
    else:
        aN, bN, aM, bM = -0.0008, 3.3, 6.471, 56.635

    if 'par_photo_N' not in kwargs:
        par_photo_N = HSExchange.par_25_N_dict()
    else:
        par_photo_N = kwargs['par_photo_N']


    modelx, psi_critx, slopex = [par_K_vul[ikey] for ikey in ('model', 'fifty_cent', 'sig_slope')]

#   Computation of the form factor matrix
    if energy_budget:
        if 'k_sky' not in g.property_names():

            print 'Computing form factors...'
    
            if not simplified_form_factors:
#               tstart = time.time()
               HSEnergy.form_factors_matrix(g, pattern, LengthConv, limit=limit)
#               print ("---%s minutes ---" % ((time.time()-tstart)/60.))
            else:
                HSEnergy.form_factors_simplified(g, pattern, leaf_lbl_prefix,
                                stem_lbl_prefix, turtle_sectors, icosphere_level,
                                unit_scene_length)

    # Rhyzosphere concentric radii
    soil_class = 'Sandy_Loam' if not 'soil_class' in kwargs else kwargs['soil_class']
    rhyzo_solution = True if 'rhyzo_solution' not in kwargs else kwargs['rhyzo_solution']
    print 'rhyzo_solution: %s'%rhyzo_solution
    if rhyzo_solution:
        rhyzo_number = 3 if not 'rhyzo_number' in kwargs else kwargs['rhyzo_number']
        print 'Soil class: %s'%soil_class
        dist_roots, rad_roots = (0.013, .0001) if 'roots' not in kwargs else kwargs['roots']
        if not 'rhyzo_radii' in kwargs:
            max_radius = 0.5*min(soil_dimensions[:2])/LengthConv
            rhyzo_radii = [max_radius*perc for perc in np.array(range(1,rhyzo_number+1))/float(rhyzo_number)]
        else:
            rhyzo_radii = kwargs['rhyzo_radii']
        
        if not any(item.startswith('rhyzo') for item in g.property('label').values()):
            vid_collar = HSArc.MTGbase(g,vtx_label=vtx_label)
            vid_base = HSArc.add_soil_components(g, rhyzo_number, rhyzo_radii,
                                        soil_dimensions, soil_class, vtx_label)
        else:
            g.properties()['soil_class'] = {vid:soil_class for vid in g.Ancestors(3)[1:]}
            vid_base = g.node(g.root).vid_base
            vid_collar = g.node(g.root).vid_collar

    else:
        dist_roots, rad_roots = (None, None)
        # Identifying and attaching the base node of a single MTG
        vid_collar = HSArc.MTGbase(g,vtx_label=vtx_label)
        vid_base = vid_collar

    g.node(g.root).vid_base = vid_base
    g.node(g.root).vid_collar = vid_collar

#   Initializing all xylem potential values to soil water potential
#    if not 'psi_head' in g.property_names():
    for vtx_id in traversal.pre_order2(g,vid_base):
        g.node(vtx_id).psi_head = psi_pd.psi[0]
    
#   Initializing sapflow to 0
#    if not 'Flux' in g.property_names():
    for vtx_id in traversal.pre_order2(g,vid_base):
        g.node(vtx_id).Flux = 0.

#   Addition of a soil element
    if 'Soil' not in g.properties()['label'].values():
        if 'soil_size' in kwargs:
            if kwargs['soil_size'] > 0.:
                HSArc.AddSoil(g,kwargs['soil_size'])
        else:
            HSArc.AddSoil(g,500.)

#   Suppression of undesired geometry for light and energy calculations
    geom_prop = g.properties()['geometry']
    vidkeys = []
    for vid in g.properties()['geometry']:
        n = g.node(vid)
        if not n.label.startswith(('L','other','soil')):
            vidkeys.append(vid)
    [geom_prop.pop(x) for x in vidkeys]
    g.properties()['geometry'] = geom_prop

#   Attaching optical properties to MTG elements
    g = HSCaribu.optical_prop(g, leaf_lbl_prefix=leaf_lbl_prefix,
                      stem_lbl_prefix=stem_lbl_prefix, wave_band='SW',
                      opt_prop=opt_prop)

#   Hack wind profile
    if not 'u_coef' in g.property_names():
        z_ls = [g.node(vid).TopPosition[2] for vid in g.VtxList(Scale=3) if g.node(vid).label.startswith('L')]
        z_max, z_min = max(z_ls), min(z_ls)
        z_avg = 0.5*(z_max + z_min)
        for vid in g.VtxList(Scale=3):
            if g.node(vid).label.startswith('L'):
                z_node = g.node(vid).TopPosition[2]
                g.node(vid).u_coef = 1. #0.2 + 0.8*(abs(z_node-z_avg)/(1.2*(z_max-z_min)/2.))**2.

# Estimation of Nitroen surface-based content according to Prieto et al. (2012)
    # Estimation of intercepted irradiance over past 10 days:
    if not 'Na' in g.property_names():
        print 'Computing Nitrogen profile...'
        assert (sdate-min(meteo_tab.index)).days >= 10, 'Meteorological data do not cover 10 days prior to simulation date.'
        
        ppfd10_date = sdate + dt.timedelta(days=-10)
        ppfd10t = date_range(ppfd10_date, sdate, freq='H')
        ppfd10_meteo = meteo_tab.ix[ppfd10t]
        caribu_source, RdRsH_ratio = \
            HSCaribu.irradiance_distribution(ppfd10_meteo, geo_location, E_type,
                                             tzone, turtle_sectors, turtle_format,
                                             None, scene_rotation, None)
    #       Compute irradiance interception and absorbtion
        g, caribu_scene = HSCaribu.hsCaribu(mtg=g,meteo=ppfd10_meteo,local_date=None,
                           geo_location=None, E_type=None,
                           unit_scene_length=unit_scene_length, tzone=tzone,
                           wave_band='SW', source = caribu_source, direct=False,
                           infinite=True, nz=50, dz=5, ds=0.5,
                           pattern=pattern, turtle_sectors=turtle_sectors,
                           turtle_format=turtle_format,
                           leaf_lbl_prefix=leaf_lbl_prefix,
                           stem_lbl_prefix=stem_lbl_prefix,
                           opt_prop=opt_prop, rotation_angle = scene_rotation,
                           icosphere_level=None)
                                             
        g.properties()['Ei10'] = {vid:g.node(vid).Ei*3600./10./1.e6 for vid in g.property('Ei').keys()}
    
        # Estimation of leaf surface-based nitrogen content:
        for vid in g.VtxList(Scale=3):
            if g.node(vid).label.startswith(leaf_lbl_prefix):
                g.node(vid).Na= HSExchange.leaf_Na(tt,g.node(vid).Ei10,aN,bN,aM,bM)

#==============================================================================
# Simulations
#==============================================================================
#    tstart = time.time()

    sapflow = []; sapEast=[]; sapWest=[]
    an_ls = []
    rg_ls=[]
#    psi_soil_ls = [];
    psi_stem={}; Tlc_dict={}; Ei_dict = {}; an_dict = {}; gs_dict = {}

# The time loop +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    for date in meteo.time:
        print '++++++++++++++++++++++Date',date

        # Select of meteo data
        imeteo = meteo[meteo.time==date]
        
        # Add a date index to g
#        g.date = date.to_julian_date()
        g.date = dt.datetime.strftime(date, "%Y-%m-%d %H:%M:%S")

        # Read soil water potntial at midnight
        if 'psi_soil' in kwargs:
            psi_soil = kwargs['psi_soil']
        else:
            if date.hour == 0:
                try:
                    psi_soil_init = psi_pd.ix[date.date()][0]
                    psi_soil = psi_soil_init
                except KeyError:
                    pass
    #     Estimate soil water potntial evolution due to transpiration
#            else:
#                psi_soil = HSHyd.soil_water_potential(psi_soil,g.node(vid_collar).Flux*3600.,
#                                  soil_class=soil_class,
#                                  intra_dist=intra_dist,inter_dist=inter_dist,depth=depth)
#        psi_soil_ls.append(psi_soil)

        if 'sun2scene' not in kwargs or not kwargs['sun2scene']:
            sun2scene = None
        elif kwargs['sun2scene']:
            sun2scene = HSVisu.visu(g,def_elmnt_color_dict=True,scene=Scene())

        # Compute irradiance distribution over the scene
        caribu_source, RdRsH_ratio = HSCaribu.irradiance_distribution(imeteo,
                                geo_location, E_type, tzone, turtle_sectors,
                                turtle_format, sun2scene, scene_rotation,
                                None)

        # Compute irradiance interception and absorbtion
        g, caribu_scene = HSCaribu.hsCaribu(mtg=g,meteo=imeteo,local_date=None,
                           geo_location=None, E_type=None,
                           unit_scene_length=unit_scene_length, tzone=tzone,
                           wave_band='SW', source = caribu_source, direct=False,
                           infinite=True, nz=50, dz=5, ds=0.5,
                           pattern=pattern, turtle_sectors=turtle_sectors,
                           turtle_format=turtle_format,
                           leaf_lbl_prefix=leaf_lbl_prefix,
                           stem_lbl_prefix=stem_lbl_prefix,
                           opt_prop=opt_prop, rotation_angle = scene_rotation,
                           icosphere_level=None)
        
        g.properties()['Ei'] = {vid:1.2*g.node(vid).Ei for vid in g.property('Ei').keys()}

        # Trace intercepted irradiance on each time step
        rg_ls.append(sum([g.node(vid).Ei/(0.48 * 4.6)*surface(g.node(vid).geometry)*(LengthConv**2) \
            for vid in g.property('geometry') if g.node(vid).label.startswith('L')]))
    
    
#        t_soil = HSEnergy.soil_temperature(g,imeteo,t_sky+273.15,'other')
                 #[ 0, 1, 2, 3, 4, 5, 6, 7, 8, 9,10,11,12,13,14,15,16,17,18,19,20,21,22,23]
        # Hack forcing of soil temperture (model of soil temperature under development)
        dt_soil = [-2,-2,-2,-1,-1, 0, 0, 0,10,15,30,30,30,30,30,20, 6, 5, 4, 3, 2, 1, 0,-1]
        t_soil = imeteo.Tac[0] + dt_soil[date.hour]
    
        # Climatic data for energy balance module
# TODO: Change the t_sky_eff formula (cf. Gliah et al., 2011, Heat and Mass Transfer, DOI: 10.1007/s00231-011-0780-1)
        t_sky_eff = RdRsH_ratio*t_cloud + (1-RdRsH_ratio)*t_sky
        macro_meteo = {'T_sky':t_sky_eff+273.15, 'T_soil':t_soil+273.15,
                       'T_air':imeteo.Tac[0]+273.15,'Pa':imeteo.Pa[0],
                       'u':imeteo.u[0]}

        # Updating the soil water content of the 

# The t loop ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
        iter_xylem = []
        t_error_list = []
        t_iter_list = []

        # Initialize leaf [and other elements] temperature to air temperature
        g.properties()['Tlc'] = {vid:imeteo.Tac[0] for vid in g.VtxList() if vid >0 and g.node(vid).label.startswith('L')}

        for it in range(max_iter):
            t_prev = deepcopy(g.property('Tlc'))
    
# The psi loop ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
            if hydraulic_structure:
                for ipsi in range(max_iter):
                    psi_prev = deepcopy(g.property('psi_head'))
        
                    # Computes gas-exchange fluxes. Leaf T and Psi are from prev calc loop
                    HSExchange.VineExchange(g, par_photo, par_photo_N, par_gs,
                                        imeteo, E_type2, leaf_lbl_prefix,rbt)
    
                    # Computes sapflow and hydraulic properties
                    HSHyd.hydraulic_prop(g, vtx_label=vtx_label, MassConv=MassConv,
                                      LengthConv=LengthConv,
                                      a=Kx_dict['a'],b=Kx_dict['b'],min_kmax=Kx_dict['min_kmax'])
    

#                    # Updating the soil water status
                    if ipsi <= 1000 and it <= 1000: # Arbitrary limits to guarantee minimum convergence
                        if soil_water_deficit:
                            N_iter_psi = HSHyd.xylem_water_potential(g, psi_soil,
                                          psi_min=psi_min, model=modelx, max_iter=max_iter,
                                          psi_error_crit=psi_error_threshold,vtx_label=vtx_label,
                                          LengthConv=LengthConv,fifty_cent=psi_critx,
                                          sig_slope=slopex, dist_roots=dist_roots,
                                          rad_roots=rad_roots,
                                          negligible_shoot_resistance = negligible_shoot_resistance,
                                          start_vid = vid_base, stop_vid = vid_collar)
                        else:
                            for vid in g.Ancestors(vid_collar):
                                g.node(vid).psi_head = psi_soil


                    # Computes xylem water potential
                    N_iter_psi = HSHyd.xylem_water_potential(g, psi_soil,
                                  psi_min=psi_min, model=modelx, max_iter=max_iter,
                                  psi_error_crit=psi_error_threshold,vtx_label=vtx_label,
                                  LengthConv=LengthConv,fifty_cent=psi_critx,
                                  sig_slope=slopex, dist_roots=dist_roots,
                                  rad_roots=rad_roots,
                                  negligible_shoot_resistance = negligible_shoot_resistance,
                                  start_vid = vid_collar, stop_vid = None)
    
                    psi_new = g.property('psi_head')
    
                    # Evaluation of xylem conversion creterion
                    psi_error_dict = {}
                    for vtx_id in g.property('psi_head').keys():
                        psi_error_dict[vtx_id] = abs(psi_prev[vtx_id]-psi_new[vtx_id])
    
                    psi_error = max(psi_error_dict.values())
                    iter_xylem.append(N_iter_psi)
#                    print '*******************************************************'
#                    print [vid for vid in psi_error_dict if psi_error_dict[vid]==max(psi_error_dict.values())]
                    print 'psi_error = ',round(psi_error,3), ':: Nb_iter = %d'%N_iter_psi
#                    print 'mean(Psi_x) = ', round(np.mean(g.property('psi_head').values()),3), ':: Flux = %e'%g.node(3).Flux
    
                    if psi_error < psi_error_threshold:
                        break
                    else:
                        psi_new_dict = {}
                        for vtx_id in psi_new.keys():
                            psix = psi_prev[vtx_id] + psi_step*(psi_new[vtx_id]-psi_prev[vtx_id])
                            psi_new_dict[vtx_id]=psix
                        
                        g.properties()['psi_head'] = psi_new_dict
    #                    for vtx_id in psi_new.keys():
    #                        g.node(vtx_id).psi_head = 0.5*(psi_prev[vtx_id]+psi_new[vtx_id])
    
#                    axpsi=HSVisu.property_map(g,prop='psi_head',add_head_loss=True, ax=axpsi, color='red')

            else:
                ipsi = 0
#               Computes gas-exchange fluxes. Leaf T and Psi are from prev calc loop
                HSExchange.VineExchange(g, par_photo, par_photo_N, par_gs,
                                    imeteo, E_type2, leaf_lbl_prefix,rbt)

#               Computes sapflow and hydraulic properties
                HSHyd.hydraulic_prop(g, vtx_label=vtx_label, MassConv=MassConv,
                                  LengthConv=LengthConv,
                                  a=Kx_dict['a'],b=Kx_dict['b'],min_kmax=Kx_dict['min_kmax'])


# End psi loop ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
    
#           Compute leaf temperature
            if not energy_budget:
                break
            else:
                t_iter = HSEnergy.leaf_temperature(g, macro_meteo, solo, True,
                                                   leaf_lbl_prefix, max_iter,
                                                   t_error_crit)
    
                t_iter_list.append(t_iter)
                t_new = deepcopy(g.property('Tlc'))
    
    #           Evaluation of leaf temperature conversion creterion
                error_dict={vtx:abs(t_prev[vtx]-t_new[vtx]) for vtx in g.property('Tlc').keys()}
    
                t_error = round(max(error_dict.values()),3)
                print 't_error = ', t_error, 'counter =', it
    #            print [vid for vid in error_dict if error_dict[vid]==max(error_dict.values())]
    #            print '**********'
    #            t_error_list.append(t_error)
                t_error_list.append(ipsi)
                if max(error_dict.values()) < t_error_crit:
    #                print 't_iter = %d (max %d), max_gas_xylem_iter = %d, max_xylem_iter = %d'%(it+1, max(t_iter_list)+1,max(t_error_list)+1,max(iter_xylem)+1)
                    break
    #            elif imeteo.Rg[0] < 100 and imeteo.u[0] <= 0.6:
    #                print 'Temperature loop stopped since low "Rg" and "u"'
    #                break
                else:
                    assert (it <= max_iter), 'The energy budget solution did not converge.'
                    t_new_dict = {vtx_id:0.5*(t_prev[vtx_id]+t_new[vtx_id]) for vtx_id in t_new.keys()}
                    g.properties()['Tlc'] = t_new_dict


        # Write mtg to external file
        HSArc.mtg_save(g, scene, wd, output_name)

# End t loop ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

##       Plot stuff..
        sapflow.append(g.node(vid_collar).Flux)
        sapEast.append(g.node(arm_vid['arm1']).Flux)
        sapWest.append(g.node(arm_vid['arm2']).Flux)

        an_ls.append(g.node(vid_collar).FluxC)
    
        psi_stem[date]=deepcopy(g.property('psi_head'))
        Tlc_dict[date]=deepcopy(g.property('Tlc'))
        Ei_dict[date]=deepcopy(g.property('Eabs'))
        an_dict[date]=deepcopy(g.property('An'))
        gs_dict[date]=deepcopy(g.property('gs'))

        print 'psi_soil', round(psi_soil,4)
        print 'psi_collar', round(g.node(3).psi_head,4)
        print 'psi_leaf', round(np.median([g.node(vid).psi_head for vid in g.property('gs').keys()]),4)
        print ''
#        print 'Rdiff/Rglob ', RdRsH_ratio
#        print 't_sky_eff ', t_sky_eff
        print 'flux H2O', round(g.node(vid_collar).Flux*1000.*3600.,4)
        print 'flux C2O', round(g.node(vid_collar).FluxC,4)
        print 'Tlc', round(np.median([g.node(vid).Tlc for vid in g.property('gs').keys()]),2), 'Tair =', round(imeteo.Tac[0],4)
#        print 'gs', np.median(g.property('gs').values())

# End time loop +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

#    print ("---%s minutes ---" % ((time.time()-tstart)/60.))

# Write output
    # Plant total transpiration
    sapflow, sapEast, sapWest = [np.array(flow)*3600.*1000. for i,flow in enumerate((sapflow, sapEast, sapWest))]

    # Median leaf temperature
    t_ls = [np.median(Tlc_dict[date].values()) for date in meteo.time]

    # Intercepted global radiation
    rg_ls = np.array(rg_ls)/(soil_dimensions[0]*soil_dimensions[1])

    results_dict = {
    'Rg': rg_ls,
    'An': an_ls,
    'E': sapflow,
    'sapEast': sapEast,
    'sapWest': sapWest,
    'Tleaf': t_ls
    }

    # Results DataFrame
    results_df = DataFrame(results_dict, index=meteo.time)

    # Write
    results_df.to_csv(wd+output_name+'.output', sep=';', decimal='.')

    total_time_OFF = time.time()

    print ("+++Total runtime: %s minutes ---" % ((total_time_OFF-total_time_ON)/60.))

    return