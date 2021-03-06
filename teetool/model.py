# models the trajectory data

from __future__ import print_function
import numpy as np
from numpy.linalg import det, inv, svd, pinv
from scipy.interpolate import griddata

import time, sys
import teetool as tt

import multiprocessing as mp
from functools import partial


class Model(object):
    """
    This class provides the interface to the probabilistic
    modelling of trajectories

    <description>
    """

    def __init__(self, cluster_data, settings):
        """
        cluster_data is a list of (x, Y)

        settings
        "model_type" = resampling, ML, or EM
        "ngaus": number of Gaussians to create for output
        REQUIRED for ML and EM
        "basis_type" = gaussian, bernstein
        "nbasis": number of basis functions
        """

        if "model_type" not in settings:
            raise ValueError("settings has no model_type")

        if type(settings["model_type"]) is not str:
            raise TypeError("expected string")

        if "ngaus" not in settings:
            raise ValueError("settings has no ngaus")

        if type(settings["ngaus"]) is not int:
            raise TypeError("expected int")

        if settings["model_type"] in ["ML", "EM"]:
            # required basis
            if "basis_type" not in settings:
                raise ValueError("settings has no basis_type")

            if "nbasis" not in settings:
                raise ValueError("settings has no nbasis")

            if settings["nbasis"] < 2:
                raise ValueError("nbasis should be larger than 2")

        # write global settings
        self._ndim = self._getDimension(cluster_data)

        # Fit x on a [0, 1] domain
        norm_cluster_data = self._normalise_data(cluster_data)

        # this part is specific for resampling
        if settings["model_type"] == "resampling":
            (mu_y, sig_y) = self._model_by_resampling(norm_cluster_data,
                                                      settings["ngaus"])
        elif settings["model_type"] == "ML":
            (mu_y, sig_y) = self._model_by_ml(norm_cluster_data,
                                              settings["ngaus"],
                                              settings["basis_type"],
                                              settings["nbasis"])
        elif settings["model_type"] == "EM":
            (mu_y, sig_y) = self._model_by_em(norm_cluster_data,
                                              settings["ngaus"],
                                              settings["basis_type"],
                                              settings["nbasis"])

        else:
            raise NotImplementedError("{0} not available".format(settings["model_type"]))

        # convert to cells
        (cc, cA) = self._getGMMCells(mu_y, sig_y, settings["ngaus"])

        # store values
        self._mu_y = mu_y
        self._sig_y = sig_y
        #
        self._cc = cc
        self._cA = cA

        # create a list to store previous calculated values
        self._list_tube = []
        self._list_logp = []

    def getMean(self):
        """
        returns the average trajectory [x, y, (z)]
        """

        ndim = self._ndim

        mu_y = self._mu_y

        Y = np.reshape(mu_y, newshape=(-1, ndim), order='F')

        if (ndim == 2):
            x = Y[:, 0]
            y = Y[:, 1]
            z = np.zeros_like(x) # fill with zeros
        elif (ndim == 3):
            x = Y[:, 0]
            y = Y[:, 1]
            z = Y[:, 2]

        Y_out = np.array([x, y, z]).T

        return Y_out


    def getSamples(self, nsamples):
        """
        return nsamples of the model
        """

        ndim = self._ndim

        mu_y = self._mu_y
        sig_y = self._sig_y

        npoints = np.size(mu_y, axis=0) / ndim

        [U, S_diag, V] = svd(sig_y)

        S = np.diag(S_diag)

        var_y = np.mat(np.real(U*np.sqrt(S)))

        xp = np.linspace(0, 1, npoints)

        cluster_data = []

        np.random.seed(seed=10) # always same results

        for n in range(nsamples):
            vecRandom = np.random.normal(size=(mu_y.shape))
            yp = mu_y + var_y * vecRandom
            Yp = np.reshape(yp, (-1, ndim), order='F')
            cluster_data.append((xp, Yp))

        return cluster_data

    def _getEllipse(self, c, A, sdwidth=1, npoints=10):
        """
        evaluates the ellipse
        """

        ndim = self._ndim

        c = np.array(c)
        A = np.mat(A)

        # find the rotation matrix and radii of the axes
        [_, s, rotation] = svd(A)

        radii = sdwidth * np.sqrt(s)

        if ndim == 2:
            # 2d
            u = np.linspace(0.0, 2.0 * np.pi, npoints)
            x = radii[0] * np.cos(u)
            y = radii[1] * np.sin(u)

            ellipse = np.empty(shape=(npoints, ndim))
            ellipse[:,0] = x
            ellipse[:,1] = y
            ellipse = np.mat(ellipse)

            ellipse = ellipse * rotation.transpose() + c.transpose()

            return np.mat(ellipse)

        if ndim == 3:
            # 3d
            # obtain sphere
            u = np.linspace(0.0, 2.0 * np.pi, npoints)
            v = np.linspace(0.0, np.pi, npoints)

            x = radii[0] * np.outer(np.cos(u), np.sin(v))
            y = radii[1] * np.outer(np.sin(u), np.sin(v))
            z = radii[2] * np.outer(np.ones_like(u), np.cos(v))

            x = x.reshape((-1,1), order='F')
            y = y.reshape((-1,1), order='F')
            z = z.reshape((-1,1), order='F')

            (nrows, ncols) = x.shape

            ap = np.zeros(shape=(nrows, ndim))

            ap[:, 0] = x.transpose()
            ap[:, 1] = y.transpose()
            ap[:, 2] = z.transpose()

            ap = np.mat(ap)

            ap = ap * rotation.transpose() + c.transpose()

            return np.mat(ap)


    def _getSample(self, c, A, nsamples=1, std=1):
        """
        returns nsamples samples of the given Gaussian
        """

        [U, S_diag, V] = svd(A)

        S = np.diag(S_diag)

        var_y = np.mat(np.real(U*np.sqrt(S)))

        ndim = self._ndim

        Y = np.zeros((nsamples, ndim))

        for i in range(nsamples):
            # obtain sample
            vecRandom = np.random.normal(size=(c.shape))
            Yi = c + var_y * (std * vecRandom)

            Y[i,:] = Yi.transpose()

        return np.mat(Y)

    def _getCoordsEllipse(self, nsamples=20, sdwidth=5):
        """
        returns an array of xy(z) coordinates

        nsamples is number of points in ellipsoid and sdwidth is the variance
        """

        ndim = self._ndim

        ngaus = len(self._cc)

        Y_list = []

        for i in range(ngaus):
            c = self._cc[i]
            A = self._cA[i]

            Yi = self._getEllipse(c, A, sdwidth, nsamples)

            Y_list.append(Yi)

        Y = np.concatenate(Y_list, axis=0)

        return np.mat(Y)

    def _eval_logp(self, Y_pos):
        """
        evaluates on a grid, aiming at the desired number of points
        """

        (npoints, _) = Y_pos.shape
        ndim = self._ndim

        # convert to list
        Y_pos_list = []

        for y in Y_pos:
            Y_pos_list.append(y)

        # create partial function (map only takes one argument)
        # ndim, cc, cA
        func = partial(tt.helpers.gauss_logLc, ndim=ndim, cc=self._cc, cA=self._cA)

        # parallel processing
        ncores = mp.cpu_count()
        p = mp.Pool(processes=ncores)

        # output - extract results
        list_val = p.map(func, Y_pos_list)

        # cleanup
        p.close()
        p.join()

        """
        # parallel processing
        p = mp.ProcessingPool()

        # output
        results = p.amap(self._gauss_logLc, Y_pos)

        while not results.ready():
            # obtain intermediate results
            print(".", end="")
            sys.stdout.flush()
            time.sleep(1)

        print("") # new line

        # extract results
        list_val = results.get()
        """

        s = np.array(list_val).squeeze()

        return s

    def _grid2points(self, xx, yy, zz=None):
        """
        returns an Y matrix for a given grid

        xx, yy, (zz) are mgrid

        """

        nx = np.size(xx, 0)
        ny = np.size(yy, 1)
        if (self._ndim == 3):
            nz = np.size(zz, 2)

        # create list;
        Y_pos = []
        Y_idx = []

        if (self._ndim == 2):
            # 2d
            for ix in range(nx):
                for iy in range(ny):
                    x1 = xx[ix, 0]
                    y1 = yy[0, iy]
                    pos = np.mat([x1, y1])
                    Y_pos.append(pos)
                    Y_idx.append([ix, iy])
        elif (self._ndim == 3):
            # 3d
            for ix in range(nx):
                for iy in range(ny):
                    for iz in range(nz):
                        x1 = xx[ix, 0, 0]
                        y1 = yy[0, iy, 0]
                        z1 = zz[0, 0, iz]
                        pos = np.mat([x1, y1, z1])
                        Y_pos.append(pos)
                        Y_idx.append([ix, iy, iz])
        else:
            raise NotImplementedError()

        Y_pos = np.concatenate(Y_pos, axis=0)
        Y_pos = np.array(Y_pos)

        #Y_idx = np.concatenate(Y_idx, axis=0)
        Y_idx = np.array(Y_idx)

        return (Y_pos, Y_idx)

    def _points2grid(self, s, Y_idx):
        """
        converts points to a matrix

        s is values np.array and Y_idx is position np.array
        """

        #print("s {0} {1}".format(np.min(s), np.max(s)))

        this_shape = np.max(Y_idx, axis=0)

        this_shape += 1  # one larger than indices

        ss = np.zeros(shape=this_shape, dtype=float)

        for i, y_idx in enumerate(Y_idx):
            # pass all positions (passes rows)

            if self._ndim is 2:
                # 2d
                [ix, iy] = y_idx
                ss[ix, iy] = s[i]
            else:
                # 3d
                [ix, iy, iz] = y_idx
                ss[ix, iy, iz] = s[i]

        #print("ss {0} {1}".format(np.min(ss), np.max(ss)))

        return ss

    def isInside_grid(self, sdwidth, xx, yy, zz=None):
        """
        evaluate if points are inside a grid

        Input parameters:
            - sdwidth
            - xx
            - yy
            - zz (when 3d)
        """

        # check values
        if not (xx.shape == yy.shape):
            raise ValueError("dimensions should equal (use np.mgrid)")

        # ** check if this has been previously calculated

        ss = None
        # pass previous calculated versions
        for [ss1, sdwidth1, xx1, yy1, zz1] in self._list_tube:
            # check if exactly the same
            if (np.all(xx1==xx) and
                np.all(yy1==yy) and
                np.all(zz1==zz) and
                np.all(sdwidth1==sdwidth)):
                # copy
                ss = ss1

        if ss is None:
            # do the calculations

            # grid2points
            (Y_pos, Y_idx) = self._grid2points(xx, yy, zz)

            # evaluate points
            s = self.isInside_pnts(Y_pos, sdwidth, nsamples=12)

            # points2grid
            ss = self._points2grid(s, Y_idx)

            # store results
            self._list_tube.append([ss, sdwidth, xx, yy, zz])


        # return values
        return ss

    def isInside_pnts(self, P, sdwidth=1, nsamples=10):
        """
        tests if points P NxD 'points' x 'dimensions' are inside the tube
        """

        ndim = self._ndim

        # obtain a list of points, representing the Gaussian and area between
        list_Y = self._get_point_cloud(sdwidth, nsamples)

        # P is an array
        P = np.array(P)

        # create partial function (map only takes one argument)
        func = partial(tt.helpers.in_hull, P)

        # parallel processing
        ncores = mp.cpu_count()
        p = mp.Pool(processes=ncores)

        # output - extract results
        list_these_inside = p.map(func, list_Y)

        # cleanup
        p.close()
        p.join()

        # convert to array
        arr_these_inside = np.array(list_these_inside).squeeze().transpose()

        # an array of bools (all FALSE, thus zeros)
        # FALSE = not inside
        # TRUE  = inside
        P_inside = np.any(arr_these_inside, axis=1)

        return P_inside


    def _get_point_cloud(self, sdwidth=1, nsamples=10):
        """
        returns a list with point clouds, representing the transition between Gaussians

        input paramters:
            - none
        """

        list_points_cloud = []

        ngaus = len(self._cc)

        # points of first Gaussian
        c = self._cc[0]
        A = self._cA[0]
        Yi = self._getEllipse(c, A, sdwidth, nsamples)

        for i in range(ngaus-1):

            # points of next Gaussian
            c = self._cc[i+1]
            A = self._cA[i+1]
            Yi1 = self._getEllipse(c, A, sdwidth, nsamples)

            # this is the 'cloud' to test
            Y = np.concatenate((Yi, Yi1), axis=0)

            # remove duplicates
            Y = tt.helpers.unique_rows(Y)

            list_points_cloud.append(Y)

            # new current Gaussian is next Gaussian
            Yi = Yi1.copy()

        return list_points_cloud


    def evalLogLikelihood(self, xx, yy, zz=None):
        """
        evaluates values in this grid [2d/3d] and returns values

        example grid:
        xx, yy, zz = np.mgrid[-60:60:20j, -10:240:20j, -60:60:20j]
        """

        # check values
        if not (xx.shape == yy.shape):
            raise ValueError("dimensions should equal (use np.mgrid)")

        ss = None
        # pass previous calculated versions
        for [ss1, xx1, yy1, zz1] in self._list_logp:
            # check if exactly the same
            if ( np.all(xx1==xx) and
                 np.all(yy1==yy) and
                 np.all(zz1==zz) ):
                # copy
                ss = ss1

        if ss is None:
            # do the calculations

            # grid2points
            (Y_pos, Y_idx) = self._grid2points(xx, yy, zz)

            # evaluate points
            s = self._eval_logp(Y_pos)

            # points2grid
            ss = self._points2grid(s, Y_idx)

            # replace NaN's with minimum
            ss[np.isnan(ss)] = np.nanmin(ss)

            # store values
            self._list_logp.append([ss, xx, yy, zz])

        return ss


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

    def _model_by_resampling(self, cluster_data, ngaus):
        """
        returns (mu_y, sig_y) by resampling
        <description>
        """

        mdim = self._ndim

        # predict these values
        xp = np.linspace(0, 1, ngaus)

        yc = []  # list to put trajectories

        for (xn, Yn) in cluster_data:

            # array to fill
            yp = np.empty(shape=(ngaus, mdim))

            for d in range(mdim):
                ynd = Yn[:, d]
                yp[:, d] = np.interp(xp, xn, ynd)

            # single column
            yp1 = np.reshape(yp, (-1, 1), order='F')

            yc.append(yp1)

        # compute values

        ntraj = len(yc)  # number of trajectories

        # obtain average [mu]
        mu_y = np.zeros(shape=(mdim*ngaus, 1))

        for yn in yc:
            mu_y += yn

        mu_y = (mu_y / ntraj)

        # obtain standard deviation [sig]
        sig_y_sum = np.zeros(shape=(mdim*ngaus, mdim*ngaus))

        for yn in yc:
            sig_y_sum += (yn - mu_y) * (yn - mu_y).transpose()

        sig_y = np.mat(sig_y_sum / ntraj)

        return (mu_y, sig_y)

    def _model_by_ml(self, cluster_data, ngaus, type_basis, nbasis):
        """
        returns (mu_y, sig_y) by maximum likelihood (no noise assumed)

        <description>
        """

        ndim = self._ndim
        ntraj = len(cluster_data)

        # create a basis
        basis = tt.basis.Basis(type_basis, nbasis, ndim)

        wc = []

        for i, (xn, Y) in enumerate(cluster_data):
            yn = np.reshape(Y, newshape=(-1,1), order='F')
            Hn = basis.get(xn)
            wn = pinv(Hn) * yn
            wn = np.mat(wn)
            wc.append(wn)

        # obtain average [mu]
        mu_w = np.zeros(shape=(ndim*nbasis, 1))

        for wn in wc:
            mu_w += wn

        mu_w = np.mat(mu_w / ntraj)

        # obtain standard deviation [sig]
        sig_w_sum = np.zeros(shape=(ndim*nbasis, ndim*nbasis))

        for wn in wc:
            sig_w_sum += (wn - mu_w)*(wn - mu_w).transpose()

        sig_w = np.mat(sig_w_sum / ntraj)

        # predict these values
        xp = np.linspace(0, 1, ngaus)
        Hp = basis.get(xp)

        mu_y = Hp * mu_w
        sig_y = Hp * sig_w * Hp.transpose()

        return (mu_y, sig_y)

    def _model_by_em(self, cluster_data, ngaus, type_basis, nbasis):
        """
        returns (mu_y, sig_y) by expectation-maximisation
        this allows noise to be modelled due to imperfect model or actual
        measurement noise

        <description>
        """

        ndim = self._ndim
        ntraj = len(cluster_data)

        Mstar = 0
        for (xn, Yn) in cluster_data:
            Mstar += np.size(xn)

        # create a basis
        basis = tt.basis.Basis(type_basis, nbasis, ndim)

        # prepare data
        yc = []
        Hc = []

        for (xn, Yn)  in cluster_data:
            # data
            yn = np.reshape(Yn, newshape=(-1,1), order='F')
            Hn = basis.get(xn)
            # add to list
            yc.append(yn)
            Hc.append(Hn)

        # hardcoded parameters
        MAX_ITERATIONS = 2001  # maximum number of iterations
        CONV_LIKELIHOOD = 1e-3  # stop convergence
        # min_eig = 10**-6  # minimum eigenvalue (numerical trick)
        BETA_EM_LIMIT = 1e8  # maximum accuracy

        # initial variables
        BETA_EM = 1000.
        mu_w = np.zeros(shape=(nbasis*ndim, 1))
        sig_w = np.mat(np.eye(nbasis*ndim))
        sig_w_inv = inv(sig_w)

        loglikelihood_previous = np.inf

        for i_iter in range(MAX_ITERATIONS):

            Ewc = []
            Ewwc = []

            # Expectation (54) (55)
            for n  in range(ntraj):
                # data
                yn = yc[n]
                Hn = Hc[n]

                # calculate S :: (50)
                Sn_inv = sig_w_inv + np.multiply(BETA_EM,(Hn.transpose()*Hn))
                Sn = np.mat(inv(Sn_inv))

                Ewn = (Sn *((np.multiply(BETA_EM,(Hn.transpose()*yn))) + ((sig_w_inv*mu_w))))

                Ewn = np.mat(Ewn)

                # BISHOP (2.62)
                Ewnwn = Sn + Ewn*Ewn.transpose()

                Ewnwn = np.mat(Ewnwn)

                # store
                Ewc.append(Ewn);
                Ewwc.append(Ewnwn);

            #  Maximization :: (56), (57)

            # E [ MU ]
            mu_w_sum = np.zeros(shape=(nbasis*ndim, 1));

            for n  in range(ntraj):
                # extract data
                Ewn = Ewc[n]
                # sum
                mu_w_sum += Ewn

            mu_w = np.mat(mu_w_sum / ntraj)

            # E [ SIGMA ]
            sig_w_sum = np.zeros((nbasis*ndim, nbasis*ndim));

            for n  in range(ntraj):
                # extract data
                yn = yc[n]
                Hn = Hc[n]
                Ewn = Ewc[n]
                Ewnwn = Ewwc[n]

                # sum
                SIGMA_n = Ewnwn - 2.*(mu_w*Ewn.transpose()) + mu_w*mu_w.transpose()
                sig_w_sum += SIGMA_n

            sig_w = np.mat(sig_w_sum / ntraj)

            # pre-calculate inverse
            sig_w_inv = inv(sig_w)

            # E [BETA]
            BETA_sum_inv = 0.;

            for n  in range(ntraj):
                # extract data
                yn = yc[n]
                Hn = Hc[n]
                Ewn = Ewc[n]
                Ewnwn = Ewwc[n]

                BETA_sum_inv += np.dot(yn.transpose(),yn) - 2.*(np.dot(yn.transpose(),(Hn*Ewn))) + np.trace((Hn.transpose()*Hn)*Ewnwn)

            BETA_EM = np.mat((ndim*Mstar) / BETA_sum_inv)

            # ////  log likelihood ///////////

            # // ln( p(Y|w) - likelihood
            loglikelihood_pYw_sum = 0.;

            for n  in range(ntraj):
                # extract data
                yn = yc[n]
                Hn = Hc[n]
                Ewn = Ewc[n]
                Ewnwn = Ewwc[n]

                # loglikelihood_pYw_sum = loglikelihood_pYw_sum + ((yn.')*yn - 2*(yn.')*(Hn*Ewn) + trace(((Hn.')*Hn)*Ewnwn));
                loglikelihood_pYw_sum += np.dot(yn.transpose(),yn) - 2.*(np.dot(yn.transpose(),(Hn*Ewn))) + np.trace((Hn.transpose()*Hn)*Ewnwn)

            #  loglikelihood_pYw =  + ((Mstar*D) / 2) * log(2*pi) - ((Mstar*D) / 2) * log( BETA_EM ) + (BETA_EM/2) * loglikelihood_pYw_sum;
            loglikelihood_pYw = (Mstar*ndim / 2.) * np.log(2.*np.pi) - (Mstar*ndim / 2.) * np.log(BETA_EM) + (BETA_EM / 2.) * loglikelihood_pYw_sum

            # // ln( p(w) ) - prior
            loglikelihood_pw_sum = 0.;

            for n  in range(ntraj):
                # extract data
                Ewn = Ewc[n]
                Ewnwn = Ewwc[n]

                # loglikelihood_pw_sum = loglikelihood_pw_sum + trace( (LAMBDA_EM)*( Ewnwn - 2*MU_EM*(Ewn.') + (MU_EM*(MU_EM.')) ) );
                loglikelihood_pw_sum += np.trace(sig_w_inv*(Ewnwn - 2.*mu_w*Ewn.transpose() + mu_w*mu_w.transpose()))

            # loglikelihood_pw = + ((N*J*D) / 2) * log(2*pi) + (N/2) * ln_det_Sigma + (1/2) * loglikelihood_pw_sum;
            loglikelihood_pw = (ntraj*nbasis*ndim/2.)*np.log(2*np.pi) + (ntraj/2.)*np.log(det(sig_w)) + (1./2.)*loglikelihood_pw_sum

            loglikelihood_pY = loglikelihood_pYw + loglikelihood_pw

            # // check convergence
            loglikelihood_diff = np.abs(loglikelihood_pY - loglikelihood_previous)

            if np.isfinite(loglikelihood_pY):
                # check
                if (loglikelihood_diff < CONV_LIKELIHOOD):
                    break
            else:
                # not a valid loglikelihood
                print("warning: not a finite loglikelihood")
                break

            # output
            #if (i_iter % 100 == 0):
            #    print("{0} {1} {2}".format(i_iter, loglikelihood_pY, min_eig))

            # store previous log_likelihood
            loglikelihood_previous = loglikelihood_pY

        # predict these values
        xp = np.linspace(0, 1, ngaus)
        Hp = basis.get(xp)

        mu_y = Hp * mu_w
        sig_y = Hp * sig_w * Hp.transpose()

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

    def _getGMMCells(self, mu_y, sig_y, ngaus):
        """
        return Gaussian Mixture Model (GMM) in cells
        """

        cc = []
        cA = []

        for m in range(ngaus):
            # single cell
            (c, A) = self._getMuSigma(mu_y, sig_y, m, ngaus)

            # check for singularity
            A = tt.helpers.nearest_spd(A)

            cc.append(c)
            cA.append(A)

        return (cc, cA)


    def _getMuSigma(self, mu_y, sig_y, npoint, ngaus):
        """
        returns (mu, sigma)
        """
        # mu_y [DM x 1]
        # sig_y [DM x DM]
        D = self._ndim

        # check range
        if ((npoint < 0) or (npoint >= ngaus)):
            raise ValueError("{0}, not in [0, {1}]".format(npoint, ngaus))

        c = np.empty(shape=(D, 1))
        A = np.empty(shape=(D, D))

        # select position
        for d_row in range(D):
            c[d_row, 0] = mu_y[(npoint+d_row*ngaus), 0]
            for d_col in range(D):
                A[d_row, d_col] = sig_y[(npoint+d_row*ngaus), (npoint+d_col*ngaus)]

        return (c, A)

    def getOutline(self, sdwidth=1):
        """
        returns the outline [xmin, xmax, ymin, ymax, zmin, zmax]

        input parameters:
            - sdwidth
        """

        # get maximum outline (borrow from other module)
        outline = tt.helpers.getMaxOutline(self._ndim)

        # by adding a bit, the bounds include the edges
        sdwidth += 0.1

        # obtain a list of points
        list_points_cloud = self._get_point_cloud(sdwidth, nsamples=10)

        for Y in list_points_cloud:

            for d in range(self._ndim):
                x = Y[:, d]
                xmin = x.min()
                xmax = x.max()
                if (outline[d*2] > xmin):
                    outline[d*2] = xmin
                if (outline[d*2+1] < xmax):
                    outline[d*2+1] = xmax

        return outline
