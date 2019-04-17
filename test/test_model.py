""" A global test of hydroshoot model on potted grapevine, to secure refactoring"""
from os.path import join
from numpy.testing import assert_array_almost_equal

import data
from hydroshoot import model


def test_potted_grapevine():
    g = data.potted_syrah()
    results = model.run(g, join(data.sources_dir, ''),
                        write_result=False, psi_soil=-0.5, gdd_since_budbreak=1000.)
    ref = data.reference_time_series_output()
    # do not compare date index
    assert_array_almost_equal(ref.iloc[0, 1:], results.reset_index(drop=True).iloc[0, :], decimal=0)
