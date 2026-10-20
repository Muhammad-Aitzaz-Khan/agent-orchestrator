"""Dynamic programming exercises."""


def lcs_length(a, b):
    """Length of the longest common subsequence of strings a and b."""
    m, n = len(a), len(b)
    table = [[0] * n for _ in range(m)]
    for i in range(1, m):
        for j in range(1, n):
            if a[i - 1] == b[j - 1]:
                table[i][j] = table[i - 1][j - 1] + 1
            else:
                table[i][j] = max(table[i - 1][j], table[i][j - 1])
    return table[m - 1][n - 1]


def edit_distance(a, b):
    """Minimum number of single-character inserts, deletes, or substitutions turning a into b."""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = 0
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp[m][n]


def min_coins(coins, amount):
    """Fewest coins adding up to amount, with an unlimited supply of each denomination.

    Returns 0 for amount 0, and -1 if the amount cannot be made.
    """
    best = [0] + [float("inf")] * amount
    for value in range(1, amount + 1):
        for coin in coins:
            if coin <= value:
                best[value] = min(best[value], best[value - coin] + 1)
    return best[amount]
