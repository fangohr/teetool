# models the trajectory data

from __future__ import print_function
import numpy as np
from numpy.linalg import det, inv
import pathos.multiprocessing as mp
from pathos.helpers import cpu_count
import time, sys

#from progressbar import ProgressBar


class Model(object):
    """
    This class provides the interface to the probabilistic
    modelling of trajectories

    <description>
    """

    def __init__(self, cluster_data, settings):
        """
        cluster_data is a list of (x, Y)

        settings for "model_type" = "resampling":
        "mgaus": number of Gaussians to create
        """

        # (input checked in World)

        # extract settings
        M = settings["mgaus"]

        # write global settings
        self._D = self._getDimension(cluster_data)
        self._M = M

        # Fit x on a [0, 1] domain
        norm_cluster_data = self._normalise_data(cluster_data)

        # this part is specific for resampling
        (mu_y, sig_y) = self._model_by_resampling(norm_cluster_data, M)

        # convert to cells
        (cc, cA) = self._getGMMCells(mu_y, sig_y)

        # store values
        self._cc = cc
        self._cA = cA

    def eval(self, xx, yy, zz=None):
        """
        evaluates values in this grid [2d/3d] and returns values

        example grid:
        xx, yy, zz = np.mgrid[-60:60:20j, -10:240:20j, -60:60:20j]
        """

        # check values
        if not (xx.shape == yy.shape):
            raise ValueError("dimensions should equal (use np.mgrid)")

        nx = np.size(xx, 0)
        ny = np.size(yy, 1)
        if (self._D == 3):
            nz = np.size(zz, 2)

        # create two lists;
        # - index, idx
        # - position, pos
        list_idx = []
        list_pos = []

        if (self._D == 2):
            # 2d
            for ix in range(nx):
                for iy in range(ny):
                    x1 = xx[ix, 0]
                    y1 = yy[0, iy]

                    pos = np.mat([[x1], [y1]])

                    list_idx.append([ix, iy])
                    list_pos.append(pos)

        if (self._D == 3):
            # 3d
            for ix in range(nx):
                for iy in range(ny):
                    for iz in range(nz):
                        x1 = xx[ix, 0, 0]
                        y1 = yy[0, iy, 0]
                        z1 = zz[0, 0, iz]

                        pos = np.mat([[x1], [y1], [z1]])

                        list_idx.append([ix, iy, iz])
                        list_pos.append(pos)

        # parallel processing
        ncores = cpu_count()
        p = mp.ProcessingPool(ncores)

        # output
        results = p.amap(self._gauss_logLc, list_pos)

        while not results.ready():
            # obtain intermediate results
            print(".", end="")
            sys.stdout.flush()
            time.sleep(3)

        print("") # new line

        # extract results
        list_val = results.get()

        # fill values here
        if (self._D == 2):
            s = np.zeros(shape=(nx, ny))

            for (i, idx) in enumerate(list_idx):
                # copy value in matrix
                s[idx[0], idx[1]] = list_val[i]

        if (self._D == 3):
            s = np.zeros(shape=(nx, ny, nz))

            for (i, idx) in enumerate(list_idx):
                # copy value in matrix
                s[idx[0], idx[1], idx[2]] = list_val[i]

        return s

    def _normalise_data(self, cluster_data):
        """
        normalises the x dimension
        """

        # determine minimum maximum
        tuple_min_max = self._getMinMax(cluster_data)

        for (i, (x, Y)) in enumerate(cluster_data):
            x = self._getNorm(x, tuple_min_max)  # normalise
            cluster_data[i] = (x, Y)  # overwrite

        return cluster_data

    def _model_by_resampling(self, cluster_data, M):
        """
        <description>
        """

        D = self._D

        # predict these values
        xp = np.linspace(0, 1, M)

        yc = []  # list to put trajectories

        for (x, Y) in cluster_data:

            # array to fill
            yp = np.empty(shape=(M, D))

            for d in range(D):
                yd = Y[:, d]
                yp[:, d] = np.interp(xp, x, yd)

            # single column
            yp1 = np.reshape(yp, (-1, 1), order='F')

            yc.append(yp1)

        # compute values

        N = len(yc)  # number of trajectories

        # obtain average [mu]
        mu_y = np.zeros(shape=(D*M, 1))

        for yn in yc:
            mu_y += yn

        mu_y = (mu_y / N)

        # obtain standard deviation [sig]
        sig_y = np.zeros(shape=(D*M, D*M))

        for yn in yc:
            sig_y += ((yn - mu_y) * (yn - mu_y).transpose())

        sig_y = (sig_y / N)

        return (mu_y, sig_y)

    def _getMinMax(self, cluster_data):
        """
        returns tuple (xmin, xmax), to normalise data
        """
        xmin = np.inf
        xmax = -np.inf
        for (x, Y) in cluster_data:
            x1min = x.min()
            x1max = x.max()

            if (x1min < xmin):
                xmin = x1min
            if (x1max > xmax):
                xmax = x1max

        return (xmin, xmax)

    def _getNorm(self, x, tuple_min_max):
        """
        returns normalised array
        """
        (xmin, xmax) = tuple_min_max
        return ((x - xmin) / (xmax - xmin))

    def _getDimension(self, cluster_data):
        """
        returns dimension D of data
        """
        (_, Y) = cluster_data[0]
        (_, D) = Y.shape
        return D

    def _getGMMCells(self, mu_y, sig_y):
        """
        return Gaussian Mixture Model (GMM) in cells
        """

        M = self._M

        cc = []
        cA = []

        for m in range(M):
            # single cell
            (c, A) = self._getMuSigma(mu_y, sig_y, m)
            cc.append(c)
            cA.append(A)

        return (cc, cA)

    def _getMuSigma(self, mu_y, sig_y, npoint):
        """
        returns (mu, sigma)
        """
        # mu_y [DM x 1]
        # sig_y [DM x DM]
        D = self._D
        M = self._M

        # check range
        if ((npoint < 0) or (npoint >= M)):
            raise ValueError("{0}, not in [0, {1}]".format(npoint, M))

        c = np.empty(shape=(D, 1))
        A = np.empty(shape=(D, D))

        # select position
        for d_row in range(D):
            c[d_row, 0] = mu_y[(npoint+d_row*M), 0]
            for d_col in range(D):
                A[d_row, d_col] = sig_y[(npoint+d_row*M), (npoint+d_col*M)]

        return (c, A)

    def _gauss(self, y, c, A):
        """
        returns value Gaussian
        """
        D = self._D

        p1 = 1 / np.sqrt(((2*np.pi)**D)*det(A))
        p2 = np.exp(-1/2*(y-c).transpose()*inv(A)*(y-c))

        return (p1*p2)

    def _gauss_logLc(self, y):
        """
        returns the log likelihood of a position based on model (in cells)
        """

        cc = self._cc
        cA = self._cA

        if (len(cc) != len(cA)):
            raise ValueError("expected size to match")

        M = len(cc)

        """
        should be zero, but this causes log infinity
        TODO: filter these results
        """

        py = 10**-30

        for m in range(M):
            c = cc[m]
            A = cA[m]
            py += self._gauss(y, c, A)  # addition of each Gaussian

        pyL = np.log(py) - np.log(M)  # division by number of Gaussians

        return pyL
