"""二分查找：在有序序列中定位目标值的下标。

迭代版在 O(log n) 时间内完成查找，返回目标下标，找不到返回 -1。
lower_bound 版返回首个 >= target 的位置，可直接用于插入点计算。
"""

from typing import Sequence


def binary_search(nums: Sequence[int], target: int) -> int:
    """在升序序列 nums 中查找 target，返回下标，不存在返回 -1。"""
    lo, hi = 0, len(nums) - 1
    while lo <= hi:
        mid = (lo + hi) >> 1
        if nums[mid] == target:
            return mid
        if nums[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1


def lower_bound(nums: Sequence[int], target: int) -> int:
    """返回首个 >= target 的下标；若全都小于 target，返回 len(nums)。"""
    lo, hi = 0, len(nums)
    while lo < hi:
        mid = (lo + hi) >> 1
        if nums[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _self_test() -> None:
    assert binary_search([], 5) == -1
    assert binary_search([7], 7) == 0
    assert binary_search([7], 3) == -1
    assert binary_search([1, 3, 5, 7, 9], 1) == 0
    assert binary_search([1, 3, 5, 7, 9], 9) == 4
    assert binary_search([1, 3, 5, 7, 9], 6) == -1
    assert binary_search(list(range(1000)), 733) == 733

    assert lower_bound([1, 2, 2, 2, 3], 2) == 1
    assert lower_bound([1, 2, 2, 2, 3], 0) == 0
    assert lower_bound([1, 2, 2, 2, 3], 4) == 5
    print("all tests passed")


if __name__ == "__main__":
    _self_test()
    sample = [1, 3, 5, 7, 9, 11]
    print(binary_search(sample, 7))  # 3
    print(binary_search(sample, 8))  # -1
