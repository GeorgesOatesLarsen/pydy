#!usr/bin/env python

"""This file derives the non-linear equations of motion of the Carvallo-Whipple
bicycle model ([Carvallo1899]_, [Whippl1899]_) following the description and
nomenclature in [Moore2012]_.

The results are compared to the canonical values of this problem to those
presented in [BasuMandal2007]_.

.. [Moore2012] Moore, Jason K. "Human Control of a Bicycle." Doctor of
   Philosophy, University of California, Davis, 2012.
   http://moorepants.github.com/dissertation

"""


import os
from collections import OrderedDict

import numpy as np
from numpy import testing
import sympy as sm
import sympy.physics.mechanics as mec
from pydy.codegen.cython_code import CythonMatrixGenerator
from pydy.codegen.ode_function_generators import CythonODEFunctionGenerator
from dtk import bicycle

import sys
sys.path.append(os.path.dirname(__file__))

from utils import (
                   create_symbol_value_map,
                   compare_cse,
                   compare_numerical_arrays,
                   compare_numerically,
                   evalf_with_symengine,
                   evaluate_with_and_without_cse,
                   evaluate_with_autowrap,
                   formulate_equations_motion,
                   write_matrix_to_file,
                  )

# NOTE : The default cache size is sometimes too low for these large expression
# operations. This potentially helps.
os.environ['SYMPY_CACHE_SIZE'] = '6000'

##################
# Reference Frames
##################


class ReferenceFrame(mec.ReferenceFrame):
    """Subclass that enforces the desired unit vector indice style."""

    def __init__(self, *args, **kwargs):

        kwargs.pop('indices', None)
        kwargs.pop('latexs', None)

        lab = args[0].lower()
        tex = r'\hat{{{}}}_{}'

        super(ReferenceFrame, self).__init__(*args,
                                             indices=('1', '2', '3'),
                                             latexs=(tex.format(lab, '1'),
                                                     tex.format(lab, '2'),
                                                     tex.format(lab, '3')),
                                             **kwargs)


print('Defining reference frames.')

# Newtonian Frame
N = ReferenceFrame('N')
# Yaw Frame
A = ReferenceFrame('A')
# Roll Frame
B = ReferenceFrame('B')
# Rear Frame
C = ReferenceFrame('C')
# Rear Wheel Frame
D = ReferenceFrame('D')
# Front Frame
E = ReferenceFrame('E')
# Front Wheel Frame
F = ReferenceFrame('F')

####################################
# Generalized Coordinates and Speeds
####################################

# All the following are a function of time.
t = mec.dynamicsymbols._t

# q1: perpendicular distance from the n2> axis to the rear contact
#     point in the ground plane
# q2: perpendicular distance from the n1> axis to the rear contact
#     point in the ground plane
# q3: frame yaw angle
# q4: frame roll angle
# q5: frame pitch angle
# q6: rear wheel rotation angle
# q7: steering rotation angle
# q8: front wheel rotation angle
# q9: perpendicular distance from the n2> axis to the front contact
#     point in the ground plane
# q10: perpendicular distance from the n1> axis to the front contact
#     point in the ground plane

print('Defining time varying symbols.')

q1, q2, q3, q4 = mec.dynamicsymbols('q1 q2 q3 q4')
q5, q6, q7, q8 = mec.dynamicsymbols('q5 q6 q7 q8')

u1, u2, u3, u4 = mec.dynamicsymbols('u1 u2 u3 u4')
u5, u6, u7, u8 = mec.dynamicsymbols('u5 u6 u7 u8')

#################################
# Orientation of Reference Frames
#################################

print('Orienting frames.')

# TODO : Report this as a SymPy issue. The following fails:
#A.orient(N, 'Axis', (q3, A['3']))

# rear frame yaw
A.orient(N, 'Axis', (q3, N['3']))
# rear frame roll
B.orient(A, 'Axis', (q4, A['1']))
# rear frame pitch
C.orient(B, 'Axis', (q5, B['2']))
# front frame steer
E.orient(C, 'Axis', (q7, C['3']))

###########
# Constants
###########

print('Defining constants.')

# geometry
# rf: radius of front wheel
# rr: radius of rear wheel
# d1: the perpendicular distance from the steer axis to the center
#     of the rear wheel (rear offset)
# d2: the distance between wheels along the steer axis
# d3: the perpendicular distance from the steer axis to the center
#     of the front wheel (fork offset)
# l1: the distance in the c1> direction from the center of the rear
#     wheel to the frame center of mass
# l2: the distance in the c3> direction from the center of the rear
#     wheel to the frame center of mass
# l3: the distance in the e1> direction from the front wheel center to
#     the center of mass of the fork
# l4: the distance in the e3> direction from the front wheel center to
#     the center of mass of the fork

rf, rr = sm.symbols('rf rr')
d1, d2, d3 = sm.symbols('d1 d2 d3')
l1, l2, l3, l4 = sm.symbols('l1 l2 l3 l4')

# acceleration due to gravity
g = sm.symbols('g')

# mass
mc, md, me, mf = sm.symbols('mc md me mf')

# inertia
ic11, ic22, ic33, ic31 = sm.symbols('ic11 ic22 ic33 ic31')
id11, id22 = sm.symbols('id11 id22')
ie11, ie22, ie33, ie31 = sm.symbols('ie11 ie22 ie33 ie31')
if11, if22 = sm.symbols('if11 if22')

###########
# Specified
###########

# control torques
# T4 : roll torque
# T6 : rear wheel torque
# T7 : steer torque
T4, T6, T7 = mec.dynamicsymbols('T4 T6 T7')

##################
# Position Vectors
##################

print('Defining position vectors.')

# newtonian origin
no = mec.Point('no')

# newtonian origin to rear wheel center
do = mec.Point('do')
do.set_pos(no, -rr * B['3'])

# rear wheel center to bicycle frame center
co = mec.Point('co')
co.set_pos(do, l1 * C['1'] + l2 * C['3'])

# rear wheel center to steer axis point
ce = mec.Point('ce')
ce.set_pos(do, d1 * C['1'])

# steer axis point to the front wheel center
fo = mec.Point('fo')
fo.set_pos(ce, d2 * E['3'] + d3 * E['1'])

# front wheel center to front frame center
eo = mec.Point('eo')
eo.set_pos(fo, l3 * E['1'] + l4 * E['3'])

# locate the points fixed on the wheel which instaneously touch the ground
# rear
dn = mec.Point('dn')
dn.set_pos(do, rr * B['3'])
# front
fn = mec.Point('fn')
fn.set_pos(fo, rf * E['2'].cross(A['3']).cross(E['2']).normalize())

######################
# Holonomic Constraint
######################

print('Defining holonomic constraints.')

# this constraint is enforced so that the front wheel contacts the ground
holonomic = fn.pos_from(dn).dot(A['3'])

print('The holonomic constraint is a function of these dynamic variables:')
print(list(sm.ordered(mec.find_dynamicsymbols(holonomic))))

####################################
# Kinematical Differential Equations
####################################

print('Defining kinematical differential equations.')

kinematical = [q3.diff(t) - u3,  # yaw
               q4.diff(t) - u4,  # roll
               q5.diff(t) - u5,  # pitch
               q7.diff(t) - u7]  # steer

####################
# Angular Velocities
####################

print('Defining angular velocities.')

A.set_ang_vel(N, u3 * N['3'])  # yaw rate
B.set_ang_vel(A, u4 * A['1'])  # roll rate
C.set_ang_vel(B, u5 * B['2'])  # pitch rate
D.set_ang_vel(C, u6 * C['2'])  # rear wheel rate
E.set_ang_vel(C, u7 * C['3'])  # steer rate
F.set_ang_vel(E, u8 * E['2'])  # front wheel rate

###################
# Linear Velocities
###################

print('Defining linear velocities.')

# origin is fixed
no.set_vel(N, 0.0 * N['1'])

# mass centers
do.v2pt_theory(no, N, D)
co.v2pt_theory(do, N, C)
ce.v2pt_theory(do, N, C)
fo.v2pt_theory(ce, N, E)
eo.v2pt_theory(fo, N, E)

# wheel contact velocities
dn.set_vel(N, 0.0 * N['1'])
fn.v2pt_theory(fo, N, F)

####################
# Motion Constraints
####################

print('Defining nonholonomic constraints.')

nonholonomic = [fn.vel(N).dot(A['1']),
                fn.vel(N).dot(A['2']),
                fn.vel(N).dot(A['3'])]

nh1 = fn.vel(N).dot(A['3'])
nh2 = holonomic.diff(t).subs(sm.solve(kinematical, [q3.diff(t), q4.diff(t),
                                                    q5.diff(t), q7.diff(t)],
                                      dict=True)[0])
compare_numerically(nh1, nh2)  # shows that nh1 and nh2 are equivalent

print('The nonholonomic constraints are a function of these dynamic variables:')
print(list(sm.ordered(mec.find_dynamicsymbols(sm.Matrix(nonholonomic)))))

#########
# Inertia
#########

print('Defining inertia.')

# NOTE : You cannot define the wheel inertias with respect to their
# respective frames because the generalized inertia force calcs will fail
# because there is no direction cosine matrix relating the wheel frames back
# to the other reference frames so I define them here with respect to the
# rear and front frames.

# NOTE : Changing 0.0 to 0 or sm.S(0) changes the floating point errors.

Ic = mec.inertia(C, ic11, ic22, ic33, 0.0, 0.0, ic31)
Id = mec.inertia(C, id11, id22, id11, 0.0, 0.0, 0.0)
Ie = mec.inertia(E, ie11, ie22, ie33, 0.0, 0.0, ie31)
If = mec.inertia(E, if11, if22, if11, 0.0, 0.0, 0.0)

##############
# Rigid Bodies
##############

print('Defining the rigid bodies.')

rear_frame = mec.RigidBody('Rear Frame', co, C, mc, (Ic, co))
rear_wheel = mec.RigidBody('Rear Wheel', do, D, md, (Id, do))
front_frame = mec.RigidBody('Front Frame', eo, E, me, (Ie, eo))
front_wheel = mec.RigidBody('Front Wheel', fo, F, mf, (If, fo))

bodies = [rear_frame, rear_wheel, front_frame, front_wheel]

###########################
# Generalized Active Forces
###########################

print('Defining the forces and torques.')

# gravity
Fco = (co, mc * g * A['3'])
Fdo = (do, md * g * A['3'])
Feo = (eo, me * g * A['3'])
Ffo = (fo, mf * g * A['3'])

# input torques
Tc = (C, T4 * A['1'] - T6 * B['2'] - T7 * C['3'])
Td = (D, T6 * C['2'])
Te = (E, T7 * C['3'])

forces = [Fco, Fdo, Feo, Ffo, Tc, Td, Te]

###############
# Kane's Method
###############

print("Generating Kane's equations.")

# var   : J , G
# yaw   : q3, q1
# roll  : q4, q2
# pitch : q5, q4
# steer : q7, q5
# rwh   : u6, u3
# fwh   : u8, u6

kane = mec.KanesMethod(N,
                       [q3, q4, q7],  # yaw, roll, steer
                       [u4, u6, u7],  # roll rate, rear wheel rate, steer rate
                       kd_eqs=kinematical,
                       q_dependent=[q5],  # pitch angle
                       configuration_constraints=[holonomic],
                       u_dependent=[u3, u5, u8],  # yaw rate, pitch rate, front wheel rate
                       velocity_constraints=nonholonomic)

fr, frstar = kane.kanes_equations(bodies, forces)

mass_matrix = kane.mass_matrix
print('The mass matrix is a function of these dynamic variables:')
print(list(sm.ordered(mec.find_dynamicsymbols(mass_matrix))))

# sub in the kin diffs to eliminate some extraneous derivatives
forcing_vector = kane.forcing.xreplace(kane.kindiffdict())
print('The forcing vector is a function of these dynamic variables:')
print(list(sm.ordered(mec.find_dynamicsymbols(forcing_vector))))

print('Writing mass matrix and forcing vector to files.')
write_matrix_to_file(mass_matrix, 'mass_matrix.txt',
                     funcs_of_time=kane.q[:] + kane.u[:])
write_matrix_to_file(forcing_vector, 'forcing_vector.txt',
                     funcs_of_time=kane.q[:] + kane.u[:])

# Calcuate the EoMs using an independent method.
q = (q3, q4, q5, q6, q7, q8)
uI = (u4, u6, u7)  # independent generalized speeds
uD = (u3, u5, u8)  # dependent generalized speeds
u = tuple(sm.ordered(uI + uD))
u_def = {ui: qi.diff(t) for ui, qi in zip(u, q)}

#M, F = formulate_equations_motion(N, bodies, q, u_def, uI, uD, nonholonomic,
                                  #dict(forces))

print('Compare cse')

#compare_cse(F, args=[q3, q4, q5, q6, q7, q8, u3, u4, u5, u6, u7, u8, T4, T6,
                     #T7, rf, rr, d1, d2, d3, l1, l2, l3, l4, g, mc, md, me, mf,
                     #ic11, ic22, ic33, ic31, id11, id22, ie11, ie22, ie33,
                     #ie31, if11, if22])
#
#compare_cse(M, args=[q3, q4, q5, q6, q7, q8, u3, u4, u5, u6, u7, u8, T4, T6,
                     #T7, rf, rr, d1, d2, d3, l1, l2, l3, l4, g, mc, md, me, mf,
                     #ic11, ic22, ic33, ic31, id11, id22, ie11, ie22, ie33,
                     #ie31, if11, if22])

####################################
# Validation of non-linear equations
####################################

print('Loading numerical input parameters.')

# These are the Benchmark bicycle [Meijaard, et. al 2007] parameters
# reexpressed in the Moore 2012 definition.
bp = bicycle.benchmark_parameters()
mp = bicycle.benchmark_to_moore(bp)

# load the input values specified in Table 1 of [BasuMandal2007]_
basu_input = bicycle.basu_table_one_input()

# convert the Basu-Mandal values to my coordinates and speeds
moore_input = bicycle.basu_to_moore_input(basu_input, bp['rR'], bp['lam'])

# build dictionaries that map the Moore symbolic parameters to the converted
# Basu-Mandal values
constant_substitutions = OrderedDict()
for k, v in mp.items():
    try:
        exec('constant_substitutions[{}] = v'.format(k))
        #constant_substitutions[sm.Symbol(k)] = v
    except NameError:
        print('{} not added to sub dict.'.format(k))

dynamic_substitutions = {}
for k, v in moore_input.items():
    try:
        exec('dynamic_substitutions[{}] = v'.format(k))
    except NameError:
        print('{} not added to sub dict.'.format(k))
    # TODO : try this to ensure we are using 0.0 instead of other tiny floats.
    #else:
        #if abs(dynamic_substitutions[k]) < 1e-14:
            #dynamic_substitutions[k] = 0.0

specified_subs = {T4: 0.0, T6: 0.0, T7: 0.0}

# TODO : there are variables defined in create_symbol_map that are needed in
# this script.

constants_name_map = {sym.name: sym for sym in (rf, rr, d1, d2, d3,
                                                l1, l2, l3, l4, g,
                                                mc, md, me, mf,
                                                ic11, ic22, ic33, ic31,
                                                id11, id22,
                                                ie11, ie22, ie33, ie31,
                                                if11, if22)}

time_varying_name_map = {s.name: s for s in (q1, q2, q3, q4, q5, q6, q7, q8,
                                             u1, u2, u3, u4, u5, u6, u7, u8,
                                             T4, T6, T7)}

constants_substituions, dynamic_substitution, specified_subs = \
    create_symbol_value_map(constants_name_map, time_varying_name_map)

substitutions = specified_subs.copy()
substitutions.update(constant_substitutions)
substitutions.update(dynamic_substitutions)

# Two things learned here:
# 1. compilation fails if cse is not true when [mass_matrix
# 2. the resulting matrices from the c code produces slightly different M and F
# matrices. Not sure why yet. But it needs to be addressed.

print('Evaluating numerically with symengine')
M_exact = evalf_with_symengine(mass_matrix, substitutions)
F_exact = evalf_with_symengine(forcing_vector, substitutions)

print('Evaluating numerically with xreplace')
M_from_xreplace = sm.matrix2numpy(mass_matrix.xreplace(substitutions),
                                  dtype=float)
F_from_xreplace = sm.matrix2numpy(forcing_vector.xreplace(substitutions),
                                  dtype=float)

compare_numerical_arrays(M_exact, M_from_xreplace,
                         name='Mass matrix from xreplace')
compare_numerical_arrays(F_exact, F_from_xreplace,
                         name='Forcing vector from xreplace')

#print('Evaluating with autowrap C')
#M_autowrap_c = evaluate_with_autowrap(mass_matrix, substitutions, language="C")
#F_autowrap_c = evaluate_with_autowrap(forcing_vector, substitutions,
                                      #language="C")
#compare_numerical_arrays(M_exact, M_autowrap_c,
                         #name='Mass matrix from autowrap C')
#compare_numerical_arrays(F_exact, F_autowrap_c,
                         #name='Forcing vector from autowrap C')
#
#print('Evaluating with autowrap Fortran')
#M_autowrap_fortran = evaluate_with_autowrap(mass_matrix, substitutions,
                                            #language="Fortran")
#F_autowrap_fortran = evaluate_with_autowrap(forcing_vector, substitutions,
                                            #language="Fortran")
#compare_numerical_arrays(M_exact, M_autowrap_fortran,
                         #name='Mass matrix from autowrap Fortran')
#compare_numerical_arrays(F_exact, F_autowrap_fortran,
                         #name='Forcing vector from autowrap Fortran')

# this runs with only the mass matrix but uses like 10+ GB of memory to compile
# both of these show descrepancies in the steer equation u7
#M_no_cse, M_with_cse = evaluate_with_and_without_cse(mass_matrix,
                                                     #substitutions)
#F_no_cse, F_with_cse = evaluate_with_and_without_cse(forcing_vector,
                                                     #substitutions)
#compare_numerical_arrays(M_no_cse, M_with_cse,
                         #name='Mass matrix from CythonMatrixGenerator with cse')
#compare_numerical_arrays(F_no_cse, F_with_cse,
                         #name='Forcing vector from CythonMatrixGenerator with cse')

print('The state derivatives from high precision evaluation:')
xd_from_sub = np.squeeze(np.linalg.solve(M_exact, F_exact))
print(xd_from_sub)

# BUGS to report:
# 1. find_dynamicsymbols should deal with Vectors and lists of
# exprs/vectors/etc.

print('Generating a right hand side function.')

rhs_of_kin_diffs = sm.Matrix([kane.kindiffdict()[k] for k in kane.q.diff(t)])

g = CythonODEFunctionGenerator(forcing_vector,
                               kane.q[:],
                               kane.u[:],
                               list(constant_substitutions.keys()),
                               mass_matrix=mass_matrix,
                               coordinate_derivatives=rhs_of_kin_diffs,
                               specifieds=[T4, T6, T7],
                               constants_arg_type='array',
                               specifieds_arg_type='array',
                               tmp_dir='cython_ode_func_gen_files',
                               prefix='zz',
                               cse=True,
                               verbose=True)

# This solves for the right hand side symbolically and generates code from that
# expression.
# NOTE : This takes like 12+ hours to compile.
#g = CythonODEFunctionGenerator(kane.rhs(),
                               #kane.q[:],
                               #kane.u[:],
                               #list(constant_substitutions.keys()),
                               #specifieds=[T4, T6, T7],
                               #constants_arg_type='array',
                               #specifieds_arg_type='array')

# This also solves symbolically but cse's before the solve and it compiles
# super fast.
#g = CythonODEFunctionGenerator(forcing_vector,
                               #kane.q[:],
                               #kane.u[:],
                               #list(constant_substitutions.keys()),
                               #mass_matrix=mass_matrix,
                               #coordinate_derivatives=rhs_of_kin_diffs,
                               #specifieds=[T4, T6, T7],
                               #constants_arg_type='array',
                               #specifieds_arg_type='array',
                               #linear_sys_solver='sympy',
                               #tmp_dir='cython_ode_func_gen_files',
                               #prefix='zz',
                               #cse=True,
                               #verbose=True)
print('Generating rhs')
rhs = g.generate()

state_vals = np.array([dynamic_substitutions[x] for x in kane.q[:] +
                       kane.u[:]])
specified_vals = np.zeros(3)
constants_vals = np.array(list(constant_substitutions.values()))

xd_from_gen = rhs(state_vals, 0.0, specified_vals, constants_vals)
print('The state derivatives from code gen:')
print(xd_from_gen)

print("Generating output dictionary.")
# convert the outputs from my model to the Basu-Mandal coordinates
# TODO : raise an issue about not knowing which order the x vector is in with
# reference to M * x' = F
speed_deriv_names = [str(speed)[:-3] + 'p' for speed in kane.u[:]]

moore_output_from_sub = {k: v for k, v in zip(speed_deriv_names,
                                              list(xd_from_sub))}
moore_output_from_gen = {k: v for k, v in zip(speed_deriv_names,
                                              list(xd_from_gen)[g.num_coordinates:])}

# compute u1' and u2' manually
u1 = -rr*(u5 + u6)*sm.cos(q3)
u1p = u1.diff(t).xreplace(kane.kindiffdict())
u2 = -rr*(u5 + u6)*sm.sin(q3)
u2p = u2.diff(t).xreplace(kane.kindiffdict())

acc_subs_sub = {u5.diff(t): moore_output_from_sub['u5p'],
                u6.diff(t): moore_output_from_sub['u6p']}

acc_subs_gen = {u5.diff(t): moore_output_from_gen['u5p'],
                u6.diff(t): moore_output_from_gen['u6p']}

moore_output_from_sub['u1p'] = u1p.xreplace(acc_subs_sub).xreplace(substitutions)
moore_output_from_sub['u2p'] = u2p.xreplace(acc_subs_sub).xreplace(substitutions)

moore_output_from_gen['u1p'] = u1p.xreplace(acc_subs_gen).xreplace(substitutions)
moore_output_from_gen['u2p'] = u2p.xreplace(acc_subs_gen).xreplace(substitutions)

moore_output_from_sub.update(moore_input)
moore_output_from_gen.update(moore_input)

moore_output_basu_from_sub = bicycle.moore_to_basu(moore_output_from_sub,
                                                   bp['rR'], bp['lam'])
moore_output_basu_from_gen = bicycle.moore_to_basu(moore_output_from_gen,
                                                   bp['rR'], bp['lam'])

basu_output = bicycle.basu_table_one_output()

print("Assertions.")

for result, typ in zip([moore_output_basu_from_sub,
                        moore_output_basu_from_gen],
                       ['Results from Symengine evaluation',
                        'Results from CythonODEGenerator']):
    print(typ)
    for k, bv in basu_output.items():
        try:
            mv = float(result[k])
        except KeyError:
            print('{} was not checked.'.format(k))
        else:
            try:
                testing.assert_allclose(bv, mv)
            except AssertionError:
                print('Failed: {}:\n  Expected: {:1.16f}\n    Actual: {:1.16f}'.format(k, bv, mv))
            else:
                print('Matched: {}:\n  Expected: {:1.16f}\n    Actual: {:1.16f}'.format(k, bv, mv))
