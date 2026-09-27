from clipper.framing import choose_focus, output_time


def test_speaker_motion_selection_and_fallback():
    assert choose_focus([(.2, .09), (.8, .01)]) == ("single", [.2])
    assert choose_focus([(.2, .06), (.8, .05)]) == ("split", [.2, .8])
    assert choose_focus([(.2, 0), (.8, 0)]) == ("center", [.5])
    assert choose_focus([]) == ("center", [.5])


def test_crop_timeline_follows_silence_cuts():
    assert output_time(5, [(0, 2), (4, 6)]) == 3
    assert output_time(3, [(0, 2), (4, 6)]) == 2
