"""测试套件的公共 fixture。

历史上各测试文件用 `tmp` 作临时目录参数，但提供它的 conftest 一直没进版本库，
导致整个套件在收集阶段就报 fixture not found。这里补上别名。
"""
import pytest


@pytest.fixture
def tmp(tmp_path):
    return tmp_path
