import pytest


def add(a, b):
    return a + b


@pytest.mark.smoke
def test_add():
    assert add(2, 3) == 5


@pytest.mark.parametrize("a,b,expected", [(0, 0, 0), (-2, 2, 0), (1.5, 2.5, 4)])
def test_add_cases(a, b, expected):
    assert add(a, b) == expected


@pytest.mark.skip(reason="Exemplo de funcionalidade ainda não implementada")
def test_future_feature():
    pass
