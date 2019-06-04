# -*- coding: utf-8 -*-
"""
@author: Rami ALBASHA

Energy balance module of HydroShoot.

This module computes leaf (and eventually other elements) tempertaure of a
given plant shoot.
"""

from scipy import optimize, mean#, spatial
from sympy.solvers import nsolve
from sympy import Symbol
import time


from alinea.caribu.CaribuScene import CaribuScene
from alinea.caribu.sky_tools import turtle
import alinea.astk.icosphere as ico
import openalea.plantgl.all as pgl
from math import pi

from hydroshoot import utilities as utils


def pgl_scene(g, flip=False):
    geometry = g.property('geometry')
    scene = pgl.Scene()
    for id in geometry:
        if not flip:
            sh = pgl.Shape(geometry[id])
        else:
            sh = pgl.Shape(pgl.AxisRotated(pgl.Vector3(1,0,0),pi,geometry[id]))
        sh.id = id
        scene.add(sh)
    return scene


def get_leaves(g, leaf_lbl_prefix='L'):
    label = g.property('label')
    return [vid for vid in g.VtxList() if
                             vid > 0 and label[vid].startswith(leaf_lbl_prefix)]


def get_leaves_length(g, leaf_lbl_prefix='L', length_lbl='Length', unit_scene_length='cm'):
    """get length of leaves of g [m]"""
    conv = {'mm': 1.e-3, 'cm': 1.e-2, 'm': 1.}[unit_scene_length]
    leaves = get_leaves(g, leaf_lbl_prefix)
    length = g.property(length_lbl)
    return {k: v * conv for k, v in length.iteritems() if k in leaves}



a_PAR = 0.87
a_NIR = 0.35
a_glob = 0.6
e_sky = 1.0
e_leaf = 0.96
e_soil = 0.95
sigma = 5.670373e-8
lambda_ = 44.0e3
Cp = 29.07


def form_factors_simplified(g, pattern=None, infinite=False, leaf_lbl_prefix='L', turtle_sectors='46',
                            icosphere_level=3, unit_scene_length='cm'):
    """Computes sky and soil contribution factors (resp. k_sky and k_soil) to the energy budget equation.
    Both factors are calculated and attributed to each element of the scene.

    Args:
        g: a multiscale tree graph object
        pattern (tuple): 2D Coordinates of the domain bounding the scene for its replication.
            (xmin, ymin, xmax, ymax) scene is not bounded along z axis.
            Alternatively a *.8 file.
            if `None` (default), scene is not repeated
        infinite (bool): Whether the scene should be considered as infinite
            (see :func:`runCaribu` from `CaribuScene` package)
        leaf_lbl_prefix (str): the prefix of the leaf label
        turtle_sectors (str): number of turtle sectors
            (see :func:`turtle` from `sky_tools` package)
        icosphere_level (int): the level of refinement of the dual icosphere
            (see :func:`alinea.astk.icosphere.turtle_dome` for details)
        unit_scene_length (str): the unit of length used for scene coordinate and for pattern
            (should be one of `CaribuScene.units` default)

    Returns:


    Notes:
        This function is a simplified approximation of the form factors matrix which is calculated by the
            function :func:`form_factors_matrix`. The canopy is turned upside-down and light is projected in each
            case to estimate the respective contribution of the sky ({z}>=0) and soil ({z}<=0) to energy budget
            calculations. This is printed as pirouette-cacahuete in the console!
        When **icosphere_level** is defined, **turtle_sectors** is ignored.

    """
    geom = g.property('geometry')
    label = g.property('label')
    opts = {'SW': {vid: ((0.001, 0) if label[vid].startswith(leaf_lbl_prefix) else (0.001,)) for vid in geom}}
    if not icosphere_level:
        energy, emission, direction, elevation, azimuth = turtle.turtle(sectors=turtle_sectors, format='uoc', energy=1.)
    else:
        vert, fac = ico.turtle_dome(icosphere_level)
        direction = ico.sample_faces(vert, fac, iter=None, spheric=False).values()
        direction = [i[0] for i in direction]
        direction = map(lambda x: tuple(list(x[:2]) + [-x[2]]), direction)

    caribu_source = zip(len(direction) * [1. / len(direction)], direction)
    k_soil, k_sky, k_leaves = {}, {}, {}

    for s in ('pirouette', 'cacahuete'):
        print '... %s' % s
        if s == 'pirouette':
            scene = pgl_scene(g, flip=True)
        else:
            scene = pgl_scene(g)

        caribu_scene = CaribuScene(scene, light=caribu_source, opt=opts,
                                   scene_unit=unit_scene_length,
                                   pattern=pattern)

        # Run caribu
        raw, aggregated = caribu_scene.run(direct=True, infinite=infinite, split_face=False, simplify=True)

        if s == 'pirouette':
            k_soil_dict = aggregated['Ei']
            max_k_soil = float(max(k_soil_dict.values()))
            k_soil = {vid: k_soil_dict[vid] / max_k_soil for vid in k_soil_dict}
        elif s == 'cacahuete':
            k_sky_dict = aggregated['Ei']
            max_k_sky = float(max(k_sky_dict.values()))
            k_sky = {vid: k_sky_dict[vid] / max_k_sky for vid in k_sky_dict}

    for vid in aggregated['Ei']:
        k_leaves[vid] = max(0., 2. - (k_soil[vid] + k_sky[vid]))

    return k_soil, k_sky, k_leaves



def leaf_temperature_as_air_temperature(g, meteo, leaf_lbl_prefix='L'):
    """Basic model for leaf temperature, considered equal to air temperature for all leaves

    Args:
        g: a multiscale tree graph object
        meteo (DataFrame): forcing meteorological variables
        leaf_lbl_prefix (str): the prefix of the leaf label

    Returns:
        (dict): keys are leaves vertices ids and their values are all equal to air temperature [°C]

    """
    leaves = get_leaves(g, leaf_lbl_prefix)
    t_air = meteo.Tac[0]
    return {vid: t_air for vid in leaves}


def leaf_wind_as_air_wind(g, meteo, leaf_lbl_prefix='L'):
    """Basic model for wind speed at leaf level, considered equal to air wind speed for all leaves

    :Parameters:
    - **g**: an MTG object
    - **meteo**: (DataFrame): forcing meteorological variables.
    """
    leaves = get_leaves(g, leaf_lbl_prefix)
    u = meteo.u[0]
    return {vid: u for vid in leaves}


def _gbH(length, u):
    #                gbH = node.gb*1.37*0.9184 * Cp # Boundary layer conductance for heat [mol m2 s-1. The 0.9184 see Campbell and Norman (1998) in Gutschick (2016)
    #                gbH = 3.9 * (macro_meteo['u']/l_w)**0.5
    l_w = length * 0.72  # leaf length in the downwind direction [m]
    d_bl = 4. * (l_w / max(1.e-3, u)) ** 0.5 / 1000.  # Boundary layer thickness in [m] (Nobel, 2009 pp.337)
    # TODO: Replace the constant thermal conductivity coefficient of the air (0.026 W m-1 C-1) by a model accounting for air temperature, humidity and pressure (cf. Nobel, 2009 Appendix I)
    return 2. * 0.026 / d_bl  # Boundary layer conductance to heat [W m-2 K-1]


def heat_boundary_layer_conductance(leaves_length, wind_speed=0):
    u = {}
    if isinstance(wind_speed, dict):
        u = wind_speed
    else:
        u = {vid: wind_speed for vid in leaves_length}
    return {vid: _gbH(leaves_length[vid], u[vid]) for vid in leaves_length}


def leaf_temperature(g, meteo, t_soil, t_sky_eff, t_init=None, form_factors=None, gbh=None, ev=None, ei=None, solo=True,
                     ff_type=True, leaf_lbl_prefix='L', max_iter=100, t_error_crit=0.01, t_step=0.5):
    """
    Returns the "thermal structure", temperatures [degreeC] of each individual leaf and soil elements.

    :Parameters:
    - **g**: an MTG object
    - **meteo**: (DataFrame): forcing meteorological variables.
    - **t_soil**: (float) [degreeC] soil surface temperature
    - **t_sky_eff**: (float) [degreeC] effective sky temperature
    - **t_init**: (float or dict) [degreeC] temperature used for initialisation, given as a single scalar or a property dict.
        if None (default) meteo tair is used for all leaves
    - **form_factors**: (3-tuple of float or of dict) from factors for soil, sky and leaves (scalars or property dicts).
        if None (default) (0.5, 0.5, 0.5) is used for all leaves
    - **gbh**: (float or dict) [W m-2 K-1] boundary layer conductance for heat, given as a single scalar or a property dict.
        if None (default) a default model is called with length=10cm and wind_speed as found in meteo
    - **ev**: (float or dict) [mol m-2 s-1] evaporation flux, given as a single scalar or a property dict.
        if None (default) evaporation is set to zero for all leaves
    - **ei**: (float or dict) [mol m-2 s-1] PAR irradiance on leaves, given as a single scalar or a property dict.
        if None (default) PAR irradiance is set to zero for all leaves
    - **solo**: logical,
        - True (default), calculates energy budget for each element, assuming the temperatures of surrounding leaves constant (from previous calculation step)
        - False, computes simultaneously all temperatures using `sympy.solvers.nsolve` (**very costly!!!**)
    - **ff_type**: (bool) form factor type flag. If true fform factor for a given leaf is expected to be a single value, or a dict of ff otherwxie
    - **leaf_lbl_prefix**: string, the prefix of the label of the leaves
    - **max_iter**: integer, the allowable number of itrations (for solo=True)
    - **t_error_crit**: float, the allowable error in leaf temperature (for solo=True)
    """
    leaves = get_leaves(g, leaf_lbl_prefix)
    it = 0

    if t_init is None:
        t_init = meteo.Tac[0]
    if form_factors is None:
        form_factors = 0.5, 0.5, 0.5
    if gbh is None:
        gbh = _gbH(0.1, meteo.u[0])
    if ev is None:
        ev = 0
    if ei is None:
        ei = 0

    k_soil, k_sky, k_leaves = form_factors
    properties = {}
    for what in ('t_init', 'gbh', 'ev', 'ei', 'k_soil', 'k_sky', 'k_leaves'):
        val = eval(what)
        if isinstance(val, dict):
            properties[what] = val
        else:
            properties[what] = {vid: val for vid in leaves}

    # Macro-scale climatic data
    T_sky, T_air, T_soil, Pa = t_sky_eff + 273.15, meteo.Tac[0] + 273.15, t_soil + 273.15, meteo.Pa[0]
    # initialisation
    t_prev = properties['t_init']

#   Iterative calculation of leaves temperature
    if solo:
        t_error_trace = []
        it_step = t_step
        for it in range(max_iter):
#            T_leaves = mean(t_prev.values()) + 273.15
            t_dict = {}
#           +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
            for vid in leaves:
                E_glob = properties['ei'][vid]/(0.48*4.6) # Ei not Eabs
                ff_sky = properties['k_sky'][vid]
                ff_leaves = properties['k_leaves'][vid]
                ff_soil = properties['k_soil'][vid]
                gbH = properties['gbh'][vid]
                E = properties['ev'][vid]
                t_leaf = t_prev[vid]

                if not ff_type:
                    E_leaves = -sigma*sum([ff_leaves[ivid]*(t_prev[ivid]+273.15)**4 \
                            for ivid in ff_leaves])
                else:
#                   E_leaves = ff_leaves*sigma*(T_leaves)**4
                    E_leaves = ff_leaves*sigma*(t_leaf + 273.15)**4

                def _VineEnergyX(T_leaf):
                    E_SW = a_glob*E_glob
                    delta_E_LW = e_leaf*(ff_sky*e_sky*sigma*(T_sky)**4+\
                                         e_leaf*E_leaves+\
                                         ff_soil*e_soil*sigma*(T_soil)**4)\
                                 - 2*e_leaf*sigma*(T_leaf)**4
                    E_Y = -lambda_*E
                    E_H = -gbH*(T_leaf-T_air)
                    E_error = E_SW + delta_E_LW + E_Y + E_H
                    return E_error

                t_leaf0 = optimize.newton_krylov(_VineEnergyX, t_leaf+273.15) - 273.15
                t_dict[vid] = t_leaf0

            t_new = t_dict

#           Evaluation of leaf temperature conversion criterion
            error_dict={vtx:abs(t_prev[vtx]-t_new[vtx]) for vtx in leaves}
            
            t_error = max(error_dict.values())
            t_error_trace.append(t_error)

            if t_error < t_error_crit:
                break
            else:
                try:
                    if abs(t_error_trace[-1] - t_error_trace[-2]) < t_error_crit:
                        it_step = max(0.01, it_step/2.)
                except:
                    pass

                t_next = {}
                for vtx_id in t_new.keys():
                    tx = t_prev[vtx_id] + it_step*(t_new[vtx_id]-t_prev[vtx_id])
                    t_next[vtx_id] = tx

#               t_new_dict = {vtx_id:0.5*(t_prev[vtx_id]+t_new[vtx_id]) for vtx_id in t_new.keys()}
                t_prev = t_next

                
#                g.properties()['Tlc'] = {vtx_id:0.5*(t_prev[vtx_id]+t_new[vtx_id]) for vtx_id in t_new.keys()}


#   Matrix iterative calculation of leaves temperature ('not solo' case)
    else:
        it = 1
        t_lst = []
        t_dict={vid:Symbol('t%d'%vid) for vid in leaves}
    #    for vid in g.property('geometry').keys():
    ##        if g.node(vid).label.startswith(leaf_lbl_prefix):
    #        exec('t%d = %s' % (vid, None))
    #        locals()['t'+str(vid)] = Symbol('t'+str(vid))
    #        t_lst.append(locals()['t'+str(vid)])
    #        t_dict[vid] = locals()['t'+str(vid)]

        eq_lst = []
        t_leaf_lst = []
        for vid in leaves:
            E_glob = properties['ei'][vid] / (0.48 * 4.6)  # Ei not Eabs
            ff_sky = properties['k_sky'][vid]
            ff_leaves = properties['k_leaves'][vid]
            ff_soil = properties['k_soil'][vid]
            gbH = properties['gbh'][vid]
            E = properties['ev'][vid]
            t_leaf = t_prev[vid]

            t_leaf_lst.append(t_leaf)
            t_lst.append(t_dict[vid])

    #        exec('eq%d = %s' % (vid, None))

            eq_aux = 0.
            for ivid in ff_leaves:
                if not g.node(ivid).label.startswith('soil'):
                    eq_aux += -ff_leaves[ivid] * ((t_dict[ivid])**4)

            eq = (a_glob * E_glob +
                e_leaf * sigma * (ff_sky * e_sky * (T_sky**4) +
                e_leaf * eq_aux + ff_soil * e_soil * (T_sky**4) -
                2 * (t_dict[vid])**4) -
                lambda_ * E - gbH * Cp * (t_dict[vid] - T_air))

            eq_lst.append(eq)

        tt = time.time()
        t_leaf0_lst = nsolve(eq_lst, t_lst, t_leaf_lst, verify=False) - 273.15
        print ("---%s seconds ---" % (time.time()-tt))

        t_new = {}
        for ivid, vid in enumerate(leaves):
            t_new[vid] = float(t_leaf0_lst[ivid])
            ivid += 1



    return t_new, it


def soil_temperature(g, meteo, T_sky, soil_lbl_prefix='other'):
    """
    Returns soil temperature based on a simplified energy budget formula.

    Parameters:
    - **t_air**: air temperature in degrees.
    """
    hs, Pa, t_air = [float(meteo[x]) for x in ('hs', 'Pa', 'Tac')]
    T_air = t_air + 273.15

    node=[g.node(vid) for vid in g.property('geometry') if g.node(vid).label.startswith('other')][0]
    T_leaf = mean(g.property('Tlc').values()) + 273.15

    E_glob = node.Ei/(0.48*4.6) # Ei not Eabs
    t_soil = node.Tsoil if 'Tsoil' in node.properties() else t_air

    def _SoilEnergyX(T_soil):
        E_SW = (1-0.25)*E_glob # 0.25 is rough estimation of albedo of a bare soil
        delta_E_LW = e_soil*sigma*(1.*e_sky*(T_sky)**4 + 1.*e_leaf*T_leaf**4- ((T_soil)**4)) # hack: 0% loss to deeper soil layers
#                             k_leaves*e_leaf*sigma*(T_leaf)**4)
        E_Y = -lambda_ * 0.06 * utils.vapor_pressure_deficit(t_air, T_soil - 273.15, hs) / Pa # 0.06 is gM from Bailey 2016 AFM 218-219:146-160
        E_H = -0.5 * Cp * (T_soil-T_air) # 0.5 is gH from Bailey 2016 AFM 218-219:146-160
        E_error = E_SW + delta_E_LW + E_Y + E_H
        return E_error

    t_soil0 = optimize.newton_krylov(_SoilEnergyX, t_soil+273.15) - 273.15
#                print t_leaf,t_leaf0
    node.Tsoil = t_soil0


    return t_soil0


def forced_soil_temperature(imeteo):
    """ A very simple model of soil temperature"""
    dt_soil = [3, 3, 3, 3, 3, 3, 3, 3, 10, 15, 20, 20, 20, 20, 20, 15, 6, 5, 4, 3, 3, 3, 3, 3]
    t_soil = imeteo.Tac[0] + dt_soil[imeteo.index.hour[0]]
    return t_soil