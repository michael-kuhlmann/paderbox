import os.path

import numpy as np

try:
    from nt.utils.matlab import Mlab
    matlab_available = True
except ImportError:
    matlab_available = False
from nt.utils.numpy_utils import segment_axis

if matlab_available:
    mlab = Mlab()


def dereverb(settings_file_path, x, stop_mlab=True):
    """
    This method wraps the matlab WPE-dereverbing-method. Give it the path to
    the settings.m and the wpe.p file and your reverbed signals as numpy matrix.
    Return value will be the dereverbed signals as numpy matrix.

    .. note:: The overall settings for this method are determined in the
        settings.m file. The wpe.p needs that settings.m file as input argument
        in order to work properly. Make sure that you read your audio signals
        accordingly.

    .. warning:: The settings file name MUST be 'wpe_settings'!

    :param settings_file_path: Path to wpe_settings.m and wpe.p
    :param x: NxC Numpy matrix of read audio signals. N denotes the signals'
        number of frames and C stands for the number of channels you provide
        for that signal
    :param stop_mlab: Whether matlab connection should be closed after execution
    :return: NxC Numpy matrix of dereverbed audio signals. N and C as above.
    """
    if not matlab_available:
        raise EnvironmentError('Matlab not available')
    if not mlab.process.started:
        mlab.process.start()
    else:
        mlab.run_code('clear all;')

    settings = os.path.join(settings_file_path, "wpe_settings.m")

    # Check number of channels and set settings.m accordingly
    c = x.shape[1]
    modify_settings = False
    lines = []
    with open(settings) as infile:
        for line in infile:
            if 'num_mic = ' in line:
                if not str(c) in line:
                    line = 'num_mic = ' + str(c) + ";\n"
                    modify_settings = True
                else:
                    break  # ignore variable lines
            lines.append(line)
    if modify_settings:
        with open(settings, 'w') as outfile:
            for line in lines:
                outfile.write(line)

    # Process each utterance
    mlab.set_variable("x", x)
    mlab.set_variable("settings", settings)
    assert np.allclose(mlab.get_variable("x"), x)
    assert mlab.get_variable("settings") == settings
    mlab.run_code("addpath('" + settings_file_path + "');")

    # start wpe
    print("Dereverbing ...")
    mlab.run_code("y = wpe(x, settings);")
    # write dereverbed audio signals
    y = mlab.get_variable("y")

    if mlab.process.started and stop_mlab:
        mlab.process.stop()
    return y


def wpe(Y, epsilon=1e-6, order=15, delay=1, iterations=10):
    """

    :param Y: Stft signal (TxF)
    :param epsilon:
    :param order: Linear prediction order
    :param delay: Prediction delay
    :param iterations: Number of iterations
    :return: Dereverberated Stft signal
    """
    T, F = Y.shape
    dtype = Y.dtype
    power_spectrum = np.maximum(np.abs(Y * Y.conj()), epsilon)
    dereverberated = np.zeros_like(Y)

    for iteration in range(iterations):
        regression_coefficient = np.zeros((F, order), dtype=dtype)
        Y_norm = Y / np.sqrt(power_spectrum)
        Y_windowed = segment_axis(
            Y,
            order,
            order - 1,
            axis=0).T[..., :-delay - 1]
        Y_windowed_norm = segment_axis(Y_norm,
                                       order, order - 1,
                                       axis=0, ).T[..., :-delay - 1]
        correlation_matrix = np.einsum('...dt,...et->...de', Y_windowed_norm,
                                       Y_windowed_norm.conj())
        cross_correlation_vector = np.sum(
            Y_windowed_norm * Y_norm[order + delay:, None, :].T.conj(), axis=-1)
        for f in range(F):
            regression_coefficient[f, :] = np.linalg.solve(
                correlation_matrix[f, :, :], cross_correlation_vector[f, :])
        regression_signal = np.einsum('ab,abc->ac',
                                      regression_coefficient.conj(),
                                      Y_windowed).T
        dereverberated[order + delay:, :] = \
            Y[order + delay:, :] - regression_signal
        power_spectrum = np.maximum(
            np.abs(dereverberated * dereverberated.conj()), epsilon)

    return dereverberated


from nt.utils.math_ops import scaled_full_correlation_matrix


def _dereverberate(y, G_hat, K, Delta):
    L, N, T = y.shape
    dtype = y.dtype
    x_hat = np.copy(y)
    for l in range(L):
        for t in range(Delta+K, T):  # Some restrictions
            for tau in range(Delta, Delta + K):
                x_hat[l, :, t] -= G_hat[l, tau - Delta, :, :].conj().T.dot(y[l, :, t-tau])
    return x_hat


def _dereverberate_vectorized(y, G_hat, K, Delta):
    x_hat = np.copy(y)
    for tau in range(Delta, Delta + K):
        x_hat[:, :, K+Delta:] -= np.einsum('abc,abe->ace',
                                    G_hat[:, tau - Delta, :, :].conj(),
                                    y[..., K+Delta-tau:-tau])
    return x_hat


def _get_spatial_correlation_matrix_inverse(y):
    L, N, T = y.shape
    correlation_matrix, power = scaled_full_correlation_matrix(y)
    # Lambda_hat = correlation_matrix[:, :, :, None] * power[:, None, None, :]
    # inverse = np.zeros_like(Lambda_hat)
    # for l in range(L):
    #     for t in range(T):
    #         inverse[l, :, :, t] = np.linalg.inv(Lambda_hat[l, :, :, t])
    inverse = np.zeros_like(correlation_matrix)
    for l in range(L):
        inverse[l, :, :] = np.linalg.inv(correlation_matrix[l, :, :])

    inverse = inverse[:, :, :, None] / power[:, None, None, :]
    return inverse


def _get_crazy_matrix(Y, K, Delta):
    # A view may possibly be enough as well.
    L, N, T = Y.shape
    dtype = Y.dtype
    psi_bar = np.zeros((L, N*N*K, N, T-Delta-K+1), dtype=dtype)
    for n0 in range(N):
        for n1 in range(N):
            for tau in range(Delta, Delta + K):
                for t in range(T):
                    psi_bar[
                        :, N*N*(tau-Delta) + N*n0 + n1, n0, t-Delta-K+1
                    ] = Y[:, n1, t-tau]
    return psi_bar


def multichannel_wpe(Y, K, Delta, iterations=4):
    # K: regression_order (possibly frequency dependent)
    # Delta: prediction_delay
    # L: frequency bins
    # N: sensors
    # T: time frames
    L, N, T = Y.shape
    dtype = Y.dtype

    # Step 1
    G_hat = np.zeros((L, K, N, N), dtype=dtype)

    for _ in range(iterations):
        # Step 2
        x_hat = _dereverberate(Y, G_hat, K, Delta)
        assert x_hat.shape == (L, N, T)

        # Step 3
        # Maybe better on a subpart, due to fade in
        Lambda_hat_inverse = _get_spatial_correlation_matrix_inverse(x_hat)[:, :, :, :T-Delta-K+1]
        assert Lambda_hat_inverse.shape == (L, N, N, T-Delta-K+1)

        # Step 4
        psi_bar = _get_crazy_matrix(Y, K, Delta)
        assert psi_bar.shape == (L, N*N*K, N, T-Delta-K+1)
        # return psi_bar

        R_hat = np.einsum(
            'lmnt,lnot,lpot->lmp',
            psi_bar,
            Lambda_hat_inverse,
            psi_bar.conj()
        )
        assert R_hat.shape == (L, N*N*K, N*N*K)

        r_hat = np.einsum(
            'lmnt,lnot,lot->lm',
            psi_bar,
            Lambda_hat_inverse,
            Y[:, :, K+Delta-1:]
        )
        assert r_hat.shape == (L, N*N*K)

        # Step 5
        # the easiness of the reshape depends on the definition of psi_bar
        g_hat = np.zeros((L, N*N*K), dtype=dtype)
        for l in range(L):
            # g_hat[l, :] = np.linalg.inv(R_hat[l, :, :]).dot(r_hat[l, :])
            g_hat[l, :] = np.linalg.solve(R_hat[l, :, :], r_hat[l, :])
        assert g_hat.shape == (L, N*N*K)
        G_hat = g_hat.reshape(L, N, N, K).transpose((0, 3, 1, 2))
        assert G_hat.shape == (L, K, N, N)

    return x_hat
