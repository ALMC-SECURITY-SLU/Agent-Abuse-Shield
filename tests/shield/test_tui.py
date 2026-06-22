from almc_shield.shield import tui


def test_should_use_tui():
    assert tui._should_use_tui(None, True) is True
    assert tui._should_use_tui(None, False) is False
    assert tui._should_use_tui("status", True) is False


def test_key_to_action():
    assert tui._key_to_action("u", True) == ("update", None)
    assert tui._key_to_action("d", True) == ("disable", None)
    assert tui._key_to_action("x", True) == ("uninstall", None)
    # 'g' resuelve según el estado actual del feed
    assert tui._key_to_action("g", True) == ("feed-global", "off")
    assert tui._key_to_action("g", False) == ("feed-global", "on")
    # teclas de salida / desconocidas -> None
    assert tui._key_to_action("q", True) is None
    assert tui._key_to_action("z", True) is None
