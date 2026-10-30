def fibonacci(n):
    """生成前 n 项斐波那契数列，从 0、1 开始。"""
    a, b = 0, 1
    for _ in range(n):
        yield a
        a, b = b, a + b


if __name__ == "__main__":
    print(list(fibonacci(10)))
