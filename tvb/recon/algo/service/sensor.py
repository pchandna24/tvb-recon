# -*- coding: utf-8 -*-

import glob
import numpy
import os
import matplotlib
from itertools import product

# ensure default behavior is headless. If you want, e.g. Qt5Agg, use the
# MPLBACKEND environment variable.
# cf. http://matplotlib.org/faq/environment_variables_faq.html
from tvb.recon.io.factory import IOUtils
from tvb.recon.io.generic import GenericIO

matplotlib.use(os.environ.get('MPLBACKEND', 'Agg'))
import pylab
from tvb.recon.model.surface import Surface

SIGMA = 1.0


class SensorService(object):
    def gen_head_model(self, subjects_dir, subject, decimated=False, fs_bem_folder=False):
        surface_suffix = "surface"
        surface_prefix = subject
        hm_base = 'head_model'

        if decimated is True:
            surface_suffix = "surface_low"

        if fs_bem_folder is True:
            surface_prefix = '%s/%s/bem/watershed/%s' % (subjects_dir, subject, subject)
            hm_base = '%s/%s/bem/head_model' % (subjects_dir, subject)

            surfs_glob = '%s/%s/bem/watershed/*_%s.tri' % (subjects_dir, subject, surface_suffix)
            surfs = glob.glob(surfs_glob)

            if len(surfs) == 0:
                raise Exception('tri surfaces not found!')

        hm_temp = """# Domain Description 1.1

    Interfaces 3

    Interface Skull: "{0}_outer_skull_{1}.tri"
    Interface Cortex: "{0}_inner_skull_{1}.tri"
    Interface Head: "{0}_outer_skin_{1}.tri"

    Domains 4

    Domain Scalp: Skull -Head
    Domain Brain: -Cortex
    Domain Air: Head
    Domain Skull: Cortex -Skull
    """.format('%s' % surface_prefix, '%s' % surface_suffix)

        hm_geom = hm_base + '.geom'
        with open(hm_geom, 'w') as fd:
            fd.write(hm_temp)
        print(('%s written.' % (hm_geom,)))

        hm_cond = hm_base + '.cond'
        with open(hm_cond, 'w') as fd:
            fd.write("""# Properties Description 1.0 (Conductivities)

    Air         0.0
    Scalp       1
    Brain       1
    Skull       0.03
    """)
        print(('%s written.' % (hm_cond,)))

    def gen_dipole_triplets(self, pos):
        pos3 = numpy.repeat(pos, 3, axis=0)
        ori3 = numpy.tile(numpy.eye(3), (len(pos), 1))
        return pos3, ori3

    def gen_dipoles(self, pos, ori_or_face=None, out_fname=None):
        "Generate dipoles (or equiv. file) for OpenMEEG."
        if ori_or_face is None:
            pos, ori = self.gen_dipole_triplets(pos)
        else:
            if ori_or_face.dtype in numpy.floattypes:
                ori = ori_or_face
            else:
                surface = Surface(pos, ori_or_face, [], None)
                ori = surface.vertex_normals()
        numpy.savetxt(out_fname, numpy.c_[pos, ori], fmt='%f')

    def periodic_xyz_for_object(self, lab, val, aff, bw=0.1, doplot=False):
        "Find blob centers for object in lab volume having value val."
        # TODO handle oblique with multiple spacing
        # vox coords onto first mode
        vox_idx = numpy.argwhere(lab == val)
        xyz = aff.dot(numpy.c_[vox_idx, numpy.ones(vox_idx.shape[0])].T)[:3].T
        xyz_mean = xyz.mean(axis=0)
        xyz -= xyz_mean
        u, s, vt = numpy.linalg.svd(xyz, 0)
        xi = u[:, 0] * s[0]
        # histogram and ft to find spacing and offset
        bn, bxi_ = numpy.histogram(
            xi, numpy.r_[min(xi) - 0.5: max(xi) + 0.5: bw])
        bxi = bxi_[:-1] + bw / 2.0
        w = numpy.r_[2.0: 6.0: 1000j]
        f = (1.0 / w)[:, None]
        Bf = (numpy.exp(-2 * numpy.pi * 1j * bxi * f) * bn * bw).sum(axis=-1)
        i_peak = numpy.argmax(numpy.abs(Bf))
        theta = numpy.angle(Bf[i_peak])
        print(("[periodic_xyz_for_object]", val, 1 / f[i_peak][0], theta))
        xi_o = -theta / (2 * numpy.pi * f[i_peak])
        xi_pos = numpy.r_[xi_o: xi.max(): w[i_peak]]
        xi_neg = numpy.r_[-xi_o: -xi.min(): w[i_peak]]
        xi_pos = numpy.sort(numpy.r_[-xi_neg, xi_pos[1:]])
        xyz_pos = numpy.c_[xi_pos, numpy.zeros(
            (len(xi_pos), 2))].dot(vt) + xyz_mean
        if doplot:
            pylab.figure()
            pylab.subplot(2, 1, 1)
            pylab.plot(bxi, bn)
            pylab.subplot(2, 1, 2)
            pylab.plot(w, numpy.abs(Bf))
            pylab.subplot(2, 1, 1)
            cos_arg = 2 * numpy.pi * f[i_peak] * bxi + theta
            pylab.plot(bxi, numpy.cos(cos_arg) * bn.std() +
                       bn.mean(), 'k--', alpha=0.5)
            [pylab.axvline(xp, color='r') for xp in xi_pos]
            pylab.show()
        return xyz_pos

    # This is from tvb_make/util/gain_matrix_seeg.py
    def _gain_matrix_dipole(self, vertices: numpy.ndarray, orientations: numpy.ndarray, areas: numpy.ndarray,
                            sensors: numpy.ndarray):
        """
        Parameters
        ----------
        vertices             np.ndarray of floats of size n x 3, where n is the number of vertices
        orientations         np.ndarray of floats of size n x 3
        region_mapping       np.ndarray of ints of size n
        sensors              np.ndarray of floats of size m x 3, where m is the number of sensors
        Returns
        -------
        np.ndarray of size m x n
        """

        nverts = vertices.shape[0]
        nsens = sensors.shape[0]

        gain_mtx_vert = numpy.zeros((nsens, nverts))
        for sens_ind in range(nsens):
            a = sensors[sens_ind, :] - vertices
            na = numpy.sqrt(numpy.sum(a ** 2, axis=1))
            gain_mtx_vert[sens_ind, :] = areas * (numpy.sum(orientations * a, axis=1) / na ** 3) / (
                4.0 * numpy.pi * SIGMA)

        return gain_mtx_vert

    def _gain_matrix_inv_square(self, vertices: numpy.ndarray, areas: numpy.ndarray, sensors: numpy.ndarray):
        nverts = vertices.shape[0]
        nsens = sensors.shape[0]

        gain_mtx_vert = numpy.zeros((nsens, nverts))
        for sens_ind in range(nsens):
            a = sensors[sens_ind, :] - vertices
            na = numpy.sqrt(numpy.sum(a ** 2, axis=1))
            gain_mtx_vert[sens_ind, :] = areas / na ** 2

        return gain_mtx_vert

    def _get_verts_regions_matrix(self, nvertices: int, nregions: int, region_mapping: list):
        reg_map_mtx = numpy.zeros((nvertices, nregions), dtype=int)
        for i, region in enumerate(region_mapping):
            if region >= 0:
                reg_map_mtx[i, region] = 1

        return reg_map_mtx

    def compute_seeg_gain_matrix(self, seeg_xyz, cort_file, subcort_file, cort_rm, subcort_rm, out_gain_mat):
        genericIO = GenericIO()

        sensors = numpy.genfromtxt(seeg_xyz, usecols=[1, 2, 3])

        cort_vertices = genericIO.read_field_from_zip("vertices.txt", cort_file)
        cort_triangles = genericIO.read_field_from_zip("triangles.txt", cort_file, dtype="i")
        cort_surf = Surface(cort_vertices, cort_triangles)
        cort_normals = cort_surf.vertex_normals()
        cort_areas = cort_surf.get_vertex_areas()

        subcort_vertices = genericIO.read_field_from_zip("vertices.txt", subcort_file)
        subcort_triangles = genericIO.read_field_from_zip("triangles.txt", subcort_file, dtype="i")
        subcort_surf = Surface(subcort_vertices, subcort_triangles)
        subcort_areas = subcort_surf.get_vertex_areas()

        cort_rm = list(numpy.genfromtxt(cort_rm, usecols=[0], dtype='i'))
        subcort_rm = list(numpy.genfromtxt(subcort_rm, usecols=[0], dtype='i'))
        region_list = numpy.unique(cort_rm + subcort_rm)

        nr_regions = len(region_list)
        nr_vertices = cort_surf.vertices.shape[0] + subcort_surf.vertices.shape[0]

        verts_regions_mat = self._get_verts_regions_matrix(nr_vertices, nr_regions, cort_rm + subcort_rm)

        gain_matrix = self._gain_matrix_dipole(cort_surf.vertices, cort_normals, cort_areas, sensors)

        gain_matrix_subcort = self._gain_matrix_inv_square(subcort_surf.vertices, subcort_areas, sensors)

        gain_total = numpy.concatenate((gain_matrix, gain_matrix_subcort), axis=1)

        numpy.savetxt(out_gain_mat, gain_total @ verts_regions_mat)

    # This is from tvb-epilepsy
    def compute_sensors_projection(self, sensors_file, centers_file, out_matrix, normalize=95, ceil=True):
        sensors = numpy.genfromtxt(sensors_file, usecols=[1, 2, 3])
        centers = numpy.genfromtxt(centers_file, usecols=[1, 2, 3])

        n1 = sensors.shape[0]
        n2 = centers.shape[0]
        projection = numpy.zeros((n1, n2))
        dist = numpy.zeros((n1, n2))
        for i1, i2 in product(range(n1), range(n2)):
            dist[i1, i2] = numpy.abs(numpy.sum((sensors[i1, :] - centers[i2, :]) ** 2))
            projection[i1, i2] = 1 / dist[i1, i2]
        if normalize:
            projection /= numpy.percentile(projection, normalize)
        if ceil:
            if ceil is True:
                ceil = 1.0
            projection[projection > ceil] = ceil

        numpy.savetxt(out_matrix, projection)
