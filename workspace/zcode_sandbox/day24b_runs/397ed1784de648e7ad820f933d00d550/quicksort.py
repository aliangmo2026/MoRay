"""快速排序实现与演示。"""


def quicksort(items):
    """返回升序排列的新列表，不修改入参。"""
    if len(items) <= 1:
        return list(items)
    pivot = items[len(items) // 2]
    left = [x for x in items if x < pivot]
    middle = [x for x in items if x == pivot]
    right = [x for x in items if x > pivot]
    return quicksort(left) + middle + quicksort(right)


if __name__ == "__main__":
    data = [33, 10, 55, 71, 29, 3, 18, 42, 3, 99, 7]
    print("排序前:", data)
    print("排序后:", quicksort(data))
