"""作用域边界样本：global / nonlocal / 同名遮蔽 / 模块级同名引用。

验证混淆（尤其重命名）后整个模块仍可正常运行。
"""

_ab12cd = 10          # 模块级名字，与生成的新名字格式类似
_total = 0
shared = 5
MOD_RATE = 100


def bump_global():
    global _total
    _total += 1
    return _total


def make_counter():
    count = 0

    def inc():
        nonlocal count
        count += 1
        return count

    def dec():
        nonlocal count
        count -= 1
        return count

    return inc, dec


def shadow():
    _ab12cd = 1       # 遮蔽模块级同名常量
    x = _ab12cd + 1
    return x


def use_module_name():
    return _ab12cd


def make_pair():
    x = 1

    def getter():
        x = 99        # 嵌套作用域内遮蔽外层 x
        return x

    def get_outer():
        return x      # 引用外层 x

    return getter, get_outer


def set_shared():
    global shared
    shared = 7


def read_shared():
    return shared


def fake_local():
    shared = 1        # 局部遮蔽模块级 shared
    return shared


def outer_default():
    base = 5

    def inner(x, y=base):   # 默认值引用外层局部变量
        return x * 10 + y

    return inner


def use_module_default():
    def mul(x, k=MOD_RATE):  # 默认值引用模块级常量（不应被改名）
        return x * k

    return mul(2)


def local_mod_name():
    MOD_RATE = 7      # 局部遮蔽模块级同名常量
    return MOD_RATE


results = []
for i in range(3):
    results.append(bump_global())
inc, dec = make_counter()
results.append(inc())
results.append(inc())
results.append(dec())
results.append(shadow())
results.append(use_module_name())
g, go = make_pair()
results.append(g())
results.append(go())
set_shared()
results.append(read_shared())
results.append(fake_local())
show = outer_default()
results.append(show(3))
results.append(show(3, 1))
results.append(use_module_default())
results.append(local_mod_name())
print(tuple(results))
