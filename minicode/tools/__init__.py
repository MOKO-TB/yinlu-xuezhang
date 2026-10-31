"""minicode.tools 子包(工具定义与实现)。

v0.9.195 前这里是**命名空间包**(没有本文件)—— 源码态与 PyInstaller 态都
能 import;但路 2b 拆 PYZ 后 minicode 以源码归档 `minicode.zip` 随包走,
**zipimport 不支持命名包子包**,`from minicode.tools import ...` 在包内当场
ModuleNotFoundError(实测:planning.py 的导入链第一炸)。补上本文件把它变成
常规子包,导入路径与行为一字不变。
"""
