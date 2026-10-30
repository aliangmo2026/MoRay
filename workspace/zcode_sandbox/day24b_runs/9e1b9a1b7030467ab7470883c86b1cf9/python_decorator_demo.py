# -*- coding: utf-8 -*-
"""Python 装饰器原理演示（可直接运行：python python_decorator_demo.py）。

一句话原理：
    装饰器 = 接收一个可调用对象、返回一个可调用对象的高阶函数；
    `@deco` 只是 `f = deco(f)` 的语法糖，并且这行赋值发生在函数**定义时**
    （导入模块、执行到 def 的那一刻），不是调用时。
    包裹层靠**闭包**记住原函数，靠 `*args/**kwargs` 转发任意参数。
"""

import functools
import inspect
import time


# ---------- 1. 不加语法糖：手动包裹 ----------
def plain_wrapper(func):
    def wrapper(*args, **kwargs):
        print(f"  -> 进入 {func.__name__}(args={args}, kwargs={kwargs})")
        out = func(*args, **kwargs)
        print(f"  <- 离开 {func.__name__}，返回 {out!r}")
        return out
    return wrapper


def say_hi(name):
    return f"hi, {name}"


say_hi = plain_wrapper(say_hi)  # 等价于在 say_hi 上写 @plain_wrapper


# ---------- 2. @ 语法糖 + functools.wraps ----------
def logged(func):
    @functools.wraps(func)  # 把 __name__/__doc__/__module__/__wrapped__ 复制到 wrapper
    def wrapper(*args, **kwargs):
        print(f"  [log] 调用 {func.__qualname__}")
        return func(*args, **kwargs)
    return wrapper


@logged
def greet(name, punct="!"):
    """打个招呼。"""
    return f"hello {name}{punct}"


# ---------- 3. 带参数的装饰器：三层函数 ----------
def retry(times=3, delay=0.0):
    """最外层收装饰器参数，中间层收函数，最内层收调用参数。"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for i in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # 演示用；实际应捕获具体异常类型
                    last_exc = exc
                    print(f"  [retry] 第 {i}/{times} 次调用失败：{exc}")
                    if delay:
                        time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator


_attempts = {"n": 0}


@retry(times=3, delay=0)
def flaky():
    _attempts["n"] += 1
    if _attempts["n"] < 3:
        raise RuntimeError("模拟网络抖动")
    return "终于成功"


# ---------- 4. 类装饰器：可调用对象同样能当装饰器 ----------
class CountCalls:
    def __init__(self, func):
        functools.update_wrapper(self, func)  # 类实例也复制元信息
        self.func = func
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.func(*args, **kwargs)


@CountCalls
def square(x):
    return x * x


# ---------- 5. 叠加顺序：从下往上包裹 ----------
def tag(label):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return f"<{label}>{func(*args, **kwargs)}</{label}>"
        return wrapper
    return decorator


@tag("outer")
@tag("inner")
def content():
    return "content"


def main():
    print("1) 手动包裹（@ 的本质就是这行赋值）:")
    print("  ", say_hi("MoRay"))

    print("\n2) @logged + functools.wraps:")
    print("  ", greet("MoRay"))
    print("   元信息：", greet.__name__, "|", greet.__doc__,
          "| 原函数：", greet.__wrapped__.__name__)
    print("   签名：", inspect.signature(greet))

    print("\n3) 带参数的装饰器 retry（工厂模式，三层函数）:")
    print("  ", flaky())

    print("\n4) 类装饰器 CountCalls:")
    square(3)
    square(4)
    print("   square.calls =", square.calls)
    print("   square(5) =", square(5), "| after:", square.calls)

    print("\n5) 叠加顺序（先装饰 inner，再装饰 outer）:")
    print("  ", content())

    print("\n6) 装饰发生在“定义时”，不是“调用时”:")
    def announce(func):
        print(f"   [定义期] 装饰 {func.__name__}，返回 wrapper")
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            print(f"   [调用期] {func.__name__} 被调用")
            return func(*args, **kwargs)
        return wrapper

    @announce
    def work():
        return "done"

    print("   定义完成，还没调用")
    work()
    work()


if __name__ == "__main__":
    main()
