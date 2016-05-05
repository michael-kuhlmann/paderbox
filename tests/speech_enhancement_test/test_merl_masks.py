import unittest
import nt.speech_enhancement.merl_masks as mm
import nt.testing as tc
import numpy as np
from chainer.utils.weight_init import uniform
from chainer import Variable
from chainer.complex_functions import split_variable

input_data = dict(S=5.0+12.0j, N=3.0+4.0j)
input_data = {k: np.asarray(v)[None, None, None] for k, v in input_data.items()}

expectations = (
    (mm.ideal_binary_mask, 1.0),
    (mm.ideal_ratio_mask, 13.0/18.0),
    (mm.wiener_like_mask, 169.0/194.0),
    (mm.ideal_amplitude_mask, 13.0/8/np.sqrt(5)),
    (
        mm.phase_sensitive_mask,
        13.0/8/np.sqrt(5) * np.cos(np.arctan2(5, 12) - np.arctan2(8, 16))
    ),
    (mm.ideal_complex_mask, (5.0+12.0j)/(5.0+12.0j + 3.0+4.0j)),
    (mm.ideal_complex_mask_williamson, (5.0+12.0j)/(5.0+12.0j + 3.0+4.0j))
)


class SimpleIdealSoftMaskTests(unittest.TestCase):
    def test_single_input(self):
        for get_mask, desired_output in expectations:
            mask = get_mask(**input_data)
            mask = mask[0, 0, 0]
            tc.assert_almost_equal(
                mask, desired_output,
                err_msg='Test failed for {}'.format(get_mask.__name__)
            )


class TestSpectrumApproximation(unittest.TestCase):
    def test_phase_sensitive(self):
        S = uniform((10, 12, 14), dtype=np.complex64)
        Y = uniform((10, 12, 14), dtype=np.complex64)
        a = uniform((10, 12, 14), dtype=np.float32)

        desired = np.sum(np.abs(a*Y - S)**2) / a.size

        actual = mm.PhaseSensitiveSpectrumApproximation()(
            Variable(a),
            split_variable(Variable(Y)),
            split_variable(Variable(S))
        )

        np.testing.assert_almost_equal(actual.num, desired)
