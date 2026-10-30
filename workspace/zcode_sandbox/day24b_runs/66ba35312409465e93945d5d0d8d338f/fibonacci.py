"""打印斐波那契数列的前 10 项。"""


def fibonacci(n):
    """生成前 n 项斐波那契数列（1, 1, 2, 3, 5, ...）。"""
    a, b = 1, 1
    for _ in range(n):
        yield a
        a, b = b, a + b


if __name__ == "__main__":
    print(list(fibonacci(10)))
