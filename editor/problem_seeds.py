"""Built-in problems for ``manage.py seed_problems``.

Every problem comes with a reference solution (a complete Python program that
reads stdin and writes stdout). The expected outputs of the samples and of the
generated hidden tests are produced by running that program, so statement,
tests and solution cannot drift apart. The statements are written for this
project; the tasks themselves are the usual interview classics.
"""
import io
import random
import string
import sys

CATEGORIES = [
    ('array-hash', '数组与哈希', '数组遍历、前缀和、哈希表'),
    ('string', '字符串', '字符串处理与匹配'),
    ('stack-queue', '栈与队列', '栈、队列、单调结构'),
    ('search-sort', '二分与排序', '二分查找、排序、堆'),
    ('dp', '动态规划', '线性、背包、区间动态规划'),
    ('graph', '搜索与图', '深度/广度优先搜索、最短路、拓扑排序'),
    ('math-sim', '数学与模拟', '数论基础、矩阵模拟、回溯'),
    ('design', '数据结构设计', '按要求实现一个数据结构'),
]

STARTER_CODE = {
    'python': (
        'import sys\n'
        '\n'
        '\n'
        'def main():\n'
        '    data = sys.stdin.read().split()\n'
        '    # TODO: 在这里实现你的解法\n'
        '\n'
        '\n'
        'if __name__ == "__main__":\n'
        '    main()\n'
    ),
    'cpp': (
        '#include <bits/stdc++.h>\n'
        'using namespace std;\n'
        '\n'
        'int main() {\n'
        '    ios::sync_with_stdio(false);\n'
        '    cin.tie(nullptr);\n'
        '    // TODO: 在这里实现你的解法\n'
        '    return 0;\n'
        '}\n'
    ),
    'c': (
        '#include <stdio.h>\n'
        '#include <stdlib.h>\n'
        '#include <string.h>\n'
        '\n'
        'int main(void) {\n'
        '    /* TODO: 在这里实现你的解法 */\n'
        '    return 0;\n'
        '}\n'
    ),
    'javascript': (
        "const data = require('fs').readFileSync(0, 'utf8').split(/\\s+/).filter(Boolean);\n"
        '\n'
        '// TODO: 在这里实现你的解法\n'
    ),
}


def run_reference(program, input_text):
    """Run a trusted reference program in-process and return what it prints."""
    stdin, stdout = io.StringIO(input_text), io.StringIO()
    saved = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = stdin, stdout
    try:
        exec(compile(program, '<reference solution>', 'exec'), {'__name__': '__main__'})
    finally:
        sys.stdin, sys.stdout = saved
    return stdout.getvalue()


def _numbers(rng, count, low, high):
    return [rng.randint(low, high) for _ in range(count)]


def _line(values):
    return ' '.join(str(value) for value in values)


def _word(rng, low, high, alphabet=string.ascii_lowercase):
    return ''.join(rng.choice(alphabet) for _ in range(rng.randint(low, high)))


PROBLEMS = []


def problem(**fields):
    PROBLEMS.append(fields)


# --------------------------------------------------------------------------
# 数组与哈希
# --------------------------------------------------------------------------

def _gen_two_sum(rng):
    cases = []
    for size in (2, 5, 50, 2000, 50000):
        while True:
            values = rng.sample(range(-10 ** 9, 10 ** 9), size)
            i, j = sorted(rng.sample(range(size), 2))
            target = values[i] + values[j]
            seen = set()
            pairs = 0
            for value in values:
                if target - value in seen:
                    pairs += 1
                seen.add(value)
            if pairs == 1:
                break
        cases.append('%d %d\n%s\n' % (size, target, _line(values)))
    return cases


problem(
    slug='two-sum', title='两数之和', difficulty='easy', category='array-hash', tags='数组,哈希表',
    description=(
        '给定一个长度为 `n` 的整数数组和一个目标值 `target`，数组里**恰好有一对**下标不同的元素，'
        '它们的和等于 `target`。请找出这两个元素的下标。\n\n'
        '下标从 `0` 开始。'
    ),
    input_format='第一行是两个整数 `n` 和 `target`。\n第二行是 `n` 个整数，表示数组的元素。',
    output_format='输出一行，两个下标 `i` 和 `j`（`i < j`），用空格分隔。',
    constraints='- `2 ≤ n ≤ 10^5`\n- 元素和 `target` 的绝对值不超过 `2 × 10^9`\n- 保证答案存在且唯一',
    hint='边遍历边把「值 → 下标」记进哈希表：对当前的 `x`，看 `target - x` 是否已经出现过。',
    samples=['4 9\n2 7 11 15\n', '5 6\n3 2 4 8 1\n'],
    generate=_gen_two_sum,
    solution='''import sys


def main():
    data = sys.stdin.read().split()
    n, target = int(data[0]), int(data[1])
    seen = {}
    for i in range(n):
        x = int(data[2 + i])
        if target - x in seen:
            print(seen[target - x], i)
            return
        seen[x] = i


main()
''',
)


def _gen_top_k(rng):
    cases = []
    for count, vocabulary, k in ((1, 1, 1), (10, 3, 2), (200, 20, 5), (5000, 300, 50), (60000, 2000, 100)):
        words = [_word(rng, 1, 6) for _ in range(vocabulary)]
        text = [rng.choice(words) for _ in range(count)]
        k = min(k, len(set(text)))
        cases.append('%d %d\n%s\n' % (count, k, ' '.join(text)))
    return cases


problem(
    slug='top-k-frequent-words', title='出现次数最多的 K 个单词', difficulty='medium',
    category='array-hash', tags='哈希表,排序,堆',
    description=(
        '给定 `n` 个由小写字母组成的单词，请输出出现次数最多的 `k` 个不同的单词。\n\n'
        '按出现次数从多到少输出；次数相同的，按字典序从小到大。'
    ),
    input_format='第一行是两个整数 `n` 和 `k`。\n第二行是 `n` 个单词，用空格分隔。',
    output_format='输出 `k` 行，每行是一个单词和它出现的次数，用空格分隔。',
    constraints='- `1 ≤ n ≤ 10^5`\n- 每个单词长度在 `1` 到 `10` 之间\n- `1 ≤ k ≤` 不同单词的个数',
    hint='先用哈希表计数，再按 `(-次数, 单词)` 排序取前 `k` 个；`k` 很小时可以用堆。',
    samples=['7 2\ngo java go rust java go c\n', '4 3\nb a c a\n'],
    generate=_gen_top_k,
    solution='''import sys
from collections import Counter


def main():
    data = sys.stdin.read().split()
    n, k = int(data[0]), int(data[1])
    counts = Counter(data[2:2 + n])
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    for word, count in ranked[:k]:
        print(word, count)


main()
''',
)


def _gen_rain(rng):
    cases = ['1\n5\n', '3\n4 0 4\n', '6\n1 2 3 4 5 6\n']
    cases.append('%d\n%s\n' % (1000, _line(_numbers(rng, 1000, 0, 50))))
    cases.append('%d\n%s\n' % (100000, _line(_numbers(rng, 100000, 0, 10 ** 5))))
    return cases


problem(
    slug='trapping-rain-water', title='接雨水', difficulty='hard', category='array-hash',
    tags='双指针,数组,单调栈',
    description=(
        '有 `n` 根宽度为 `1` 的柱子排成一排，第 `i` 根的高度是 `h[i]`。下过雨后，'
        '柱子之间的凹陷处会积水。请计算一共能接住多少个单位的水。'
    ),
    input_format='第一行是整数 `n`。\n第二行是 `n` 个非负整数，表示每根柱子的高度。',
    output_format='输出一个整数，表示接住的雨水总量。',
    constraints='- `1 ≤ n ≤ 10^5`\n- `0 ≤ h[i] ≤ 10^5`',
    hint='某个位置上方的水量取决于它左边最高和右边最高中较矮的那个。用两个指针从两端向中间走，可以做到 O(1) 额外空间。',
    samples=['12\n0 1 0 2 1 0 1 3 2 1 2 1\n', '6\n4 2 0 3 2 5\n'],
    generate=_gen_rain,
    solution='''import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    h = [int(x) for x in data[1:1 + n]]
    left, right = 0, n - 1
    left_max = right_max = 0
    water = 0
    while left < right:
        if h[left] < h[right]:
            left_max = max(left_max, h[left])
            water += left_max - h[left]
            left += 1
        else:
            right_max = max(right_max, h[right])
            water += right_max - h[right]
            right -= 1
    print(water)


main()
''',
)


# --------------------------------------------------------------------------
# 字符串
# --------------------------------------------------------------------------

def _gen_reverse_words(rng):
    cases = ['single\n', '   leading and trailing   \n']
    for count in (10, 500, 20000):
        words = [_word(rng, 1, 8, string.ascii_letters + string.digits) for _ in range(count)]
        cases.append(''.join(word + ' ' * rng.randint(1, 3) for word in words).rstrip() + '\n')
    return cases


problem(
    slug='reverse-words', title='翻转单词顺序', difficulty='easy', category='string', tags='字符串,双指针',
    description=(
        '给定一行由若干单词组成的文本，单词之间用一个或多个空格分隔，行首行尾也可能有多余的空格。\n\n'
        '请把单词的顺序颠倒后输出：单词内部的字符顺序不变，单词之间只保留一个空格，行首行尾不要有空格。'
    ),
    input_format='输入只有一行文本，至少包含一个单词。',
    output_format='输出一行，单词顺序颠倒后的结果。',
    constraints='- 文本长度不超过 `2 × 10^5`\n- 单词只包含字母和数字',
    hint='按空白切分、反转、再用一个空格拼起来。不借助库函数时，可以先整体反转，再逐个单词反转。',
    samples=['the sky is blue\n', '  hello   world  \n'],
    generate=_gen_reverse_words,
    solution='''import sys


def main():
    words = sys.stdin.read().split()
    print(' '.join(reversed(words)))


main()
''',
)


def _gen_unique_substring(rng):
    cases = ['a\n', 'aaaaaaaa\n', string.ascii_lowercase + '\n']
    cases.append(_word(rng, 2000, 2000, 'abcdef') + '\n')
    cases.append(_word(rng, 100000, 100000, string.ascii_letters + string.digits) + '\n')
    return cases


problem(
    slug='longest-unique-substring', title='最长无重复字符子串', difficulty='medium', category='string',
    tags='字符串,滑动窗口,哈希表',
    description='给定一个字符串，请求出其中**不含重复字符**的最长连续子串的长度。',
    input_format='输入只有一行，是一个非空字符串，只包含字母和数字。',
    output_format='输出一个整数，表示最长无重复字符子串的长度。',
    constraints='- 字符串长度不超过 `10^5`',
    hint='滑动窗口：右端每次向右扩一格；如果新字符在窗口内出现过，就把左端移到它上次出现位置的右边。',
    samples=['abcabcbb\n', 'pwwkew\n'],
    generate=_gen_unique_substring,
    solution='''import sys


def main():
    s = sys.stdin.readline().strip()
    last = {}
    best = start = 0
    for i, ch in enumerate(s):
        if ch in last and last[ch] >= start:
            start = last[ch] + 1
        last[ch] = i
        best = max(best, i - start + 1)
    print(best)


main()
''',
)


def _gen_palindrome(rng):
    cases = ['a\n', 'ab\n', 'aaaa\n']
    cases.append(_word(rng, 300, 300, 'ab') + '\n')
    half = _word(rng, 400, 400, 'abc')
    cases.append(_word(rng, 80, 80, 'xyz') + half + half[::-1] + _word(rng, 100, 100, 'xyz') + '\n')
    return cases


problem(
    slug='longest-palindromic-substring', title='最长回文子串', difficulty='medium', category='string',
    tags='字符串,动态规划,中心扩展',
    description=(
        '给定一个字符串，请找出其中最长的回文子串（正着读和倒着读一样的连续子串）。\n\n'
        '如果有多个长度相同的，输出**起始位置最靠左**的那个。'
    ),
    input_format='输入只有一行，是一个只包含小写字母的非空字符串。',
    output_format='输出一行，即最长的回文子串。',
    constraints='- 字符串长度不超过 `1000`',
    hint='以每个字符（以及每两个相邻字符之间）为中心向两边扩展，时间复杂度 O(n²)。',
    samples=['babad\n', 'cbbd\n'],
    generate=_gen_palindrome,
    solution='''import sys


def main():
    s = sys.stdin.readline().strip()
    n = len(s)
    best_start, best_len = 0, 1
    for center in range(2 * n - 1):
        left = center // 2
        right = left + center % 2
        while left >= 0 and right < n and s[left] == s[right]:
            left -= 1
            right += 1
        length = right - left - 1
        start = left + 1
        if length > best_len or (length == best_len and start < best_start):
            best_start, best_len = start, length
    print(s[best_start:best_start + best_len])


main()
''',
)


def _gen_big_add(rng):
    cases = ['0\n0\n', '999\n1\n', '1\n99999999999999999999\n']
    for digits in (50, 1000):
        a = str(rng.randint(1, 9)) + ''.join(rng.choice(string.digits) for _ in range(digits - 1))
        b = str(rng.randint(1, 9)) + ''.join(rng.choice(string.digits) for _ in range(rng.randint(1, digits) - 1))
        cases.append('%s\n%s\n' % (a, b))
    return cases


problem(
    slug='big-number-addition', title='大整数加法', difficulty='easy', category='string', tags='字符串,模拟,数学',
    description=(
        '给定两个非负整数，它们可能非常大（远超 64 位整数的范围）。请计算它们的和。\n\n'
        '面试时请不要直接使用语言自带的大整数运算，而是按竖式加法逐位模拟。'
    ),
    input_format='输入共两行，每行一个不含前导零的非负整数（`0` 本身除外）。',
    output_format='输出一行，表示两数之和，不含前导零。',
    constraints='- 每个整数最多有 `1000` 位',
    hint='从个位开始逐位相加并记录进位；最后别忘了处理最高位的进位。',
    samples=['123\n989\n', '99999999999999999999\n1\n'],
    generate=_gen_big_add,
    solution='''import sys


def main():
    a, b = sys.stdin.read().split()
    i, j, carry = len(a) - 1, len(b) - 1, 0
    digits = []
    while i >= 0 or j >= 0 or carry:
        total = carry
        if i >= 0:
            total += ord(a[i]) - 48
            i -= 1
        if j >= 0:
            total += ord(b[j]) - 48
            j -= 1
        digits.append(chr(48 + total % 10))
        carry = total // 10
    print(''.join(reversed(digits)))


main()
''',
)


# --------------------------------------------------------------------------
# 栈与队列
# --------------------------------------------------------------------------

def _gen_brackets(rng):
    def balanced(length):
        pairs = {'(': ')', '[': ']', '{': '}'}
        result, stack = [], []
        while len(result) < length:
            remaining = length - len(result)
            if stack and (len(stack) == remaining or rng.random() < 0.45):
                result.append(stack.pop())
            else:
                opener = rng.choice('([{')
                result.append(opener)
                stack.append(pairs[opener])
        return ''.join(result)

    cases = ['(\n', ')(\n', '([)]\n', balanced(1000) + '\n']
    broken = list(balanced(100000))
    broken[rng.randrange(len(broken))] = rng.choice('([{)]}')
    cases.append(''.join(broken) + '\n')
    cases.append(balanced(100000) + '\n')
    return cases


problem(
    slug='valid-brackets', title='括号是否匹配', difficulty='easy', category='stack-queue', tags='栈,字符串',
    description=(
        '给定一个只包含 `(`、`)`、`[`、`]`、`{`、`}` 的字符串，判断其中的括号是否正确匹配：\n\n'
        '- 每个左括号都有同类型的右括号与之闭合；\n'
        '- 括号必须按正确的顺序闭合，不能交叉。'
    ),
    input_format='输入只有一行，是一个非空的括号字符串。',
    output_format='匹配输出 `YES`，否则输出 `NO`。',
    constraints='- 字符串长度不超过 `10^5`',
    hint='遇到左括号入栈；遇到右括号时，栈顶必须是与之对应的左括号。最后栈必须为空。',
    samples=['()[]{}\n', '([)]\n'],
    generate=_gen_brackets,
    solution='''import sys


def main():
    s = sys.stdin.readline().strip()
    pairs = {')': '(', ']': '[', '}': '{'}
    stack = []
    for ch in s:
        if ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                print('NO')
                return
        else:
            stack.append(ch)
    print('NO' if stack else 'YES')


main()
''',
)


def _gen_min_stack(rng):
    cases = []
    for count in (5, 200, 60000):
        lines, size = [], 0
        for _ in range(count):
            roll = rng.random()
            if size == 0 or roll < 0.45:
                lines.append('push %d' % rng.randint(-10 ** 9, 10 ** 9))
                size += 1
            elif roll < 0.6:
                lines.append('pop')
                size -= 1
            elif roll < 0.8:
                lines.append('top')
            else:
                lines.append('min')
        cases.append('%d\n%s\n' % (len(lines), '\n'.join(lines)))
    return cases


problem(
    slug='min-stack', title='最小栈', difficulty='easy', category='stack-queue', tags='栈,设计',
    description=(
        '实现一个栈，除了常规的入栈、出栈、取栈顶之外，还要能在**常数时间**内取出栈中的最小元素。\n\n'
        '需要支持的操作：\n\n'
        '- `push x`：把整数 `x` 压入栈；\n'
        '- `pop`：弹出栈顶元素；\n'
        '- `top`：输出栈顶元素；\n'
        '- `min`：输出栈中的最小元素。'
    ),
    input_format='第一行是操作个数 `q`。\n接下来 `q` 行，每行一个操作。',
    output_format='对每个 `top` 和 `min` 操作输出一行结果。',
    constraints='- `1 ≤ q ≤ 10^5`\n- `x` 的绝对值不超过 `10^9`\n- 保证 `pop`、`top`、`min` 执行时栈不为空',
    hint='再维护一个「最小值栈」：每次入栈时，把「当前元素与此前最小值中较小的那个」也压进去，两个栈同步出栈。',
    samples=['7\npush 3\npush 5\nmin\npush 2\nmin\npop\nmin\n', '4\npush -1\ntop\npush -1\nmin\n'],
    generate=_gen_min_stack,
    solution='''import sys


def main():
    lines = sys.stdin.read().split('\\n')
    q = int(lines[0])
    stack, mins, out = [], [], []
    for line in lines[1:1 + q]:
        parts = line.split()
        if parts[0] == 'push':
            x = int(parts[1])
            stack.append(x)
            mins.append(x if not mins else min(x, mins[-1]))
        elif parts[0] == 'pop':
            stack.pop()
            mins.pop()
        elif parts[0] == 'top':
            out.append(str(stack[-1]))
        else:
            out.append(str(mins[-1]))
    print('\\n'.join(out))


main()
''',
)


def _gen_window_max(rng):
    cases = ['1 1\n7\n', '5 5\n1 2 3 4 5\n', '6 1\n3 1 4 1 5 9\n']
    cases.append('1000 37\n%s\n' % _line(_numbers(rng, 1000, -1000, 1000)))
    cases.append('100000 5000\n%s\n' % _line(_numbers(rng, 100000, -10 ** 9, 10 ** 9)))
    cases.append('100000 3\n%s\n' % _line(range(100000, 0, -1)))
    return cases


problem(
    slug='sliding-window-maximum', title='滑动窗口最大值', difficulty='hard', category='stack-queue',
    tags='单调队列,滑动窗口',
    description=(
        '给定一个长度为 `n` 的整数数组和窗口大小 `k`。窗口从数组最左端开始，每次向右滑动一格，'
        '直到到达最右端。请输出每个位置上窗口内的最大值。'
    ),
    input_format='第一行是两个整数 `n` 和 `k`。\n第二行是 `n` 个整数。',
    output_format='输出一行，共 `n - k + 1` 个整数，用空格分隔。',
    constraints='- `1 ≤ k ≤ n ≤ 10^5`\n- 元素的绝对值不超过 `10^9`',
    hint='用双端队列存下标，并保持队列里的值单调递减：队首就是当前窗口的最大值，滑出窗口的下标从队首弹出。',
    samples=['8 3\n1 3 -1 -3 5 3 6 7\n', '4 2\n9 8 7 6\n'],
    generate=_gen_window_max,
    solution='''import sys
from collections import deque


def main():
    data = sys.stdin.read().split()
    n, k = int(data[0]), int(data[1])
    a = [int(x) for x in data[2:2 + n]]
    window = deque()
    out = []
    for i, x in enumerate(a):
        while window and a[window[-1]] <= x:
            window.pop()
        window.append(i)
        if window[0] <= i - k:
            window.popleft()
        if i >= k - 1:
            out.append(str(a[window[0]]))
    print(' '.join(out))


main()
''',
)


# --------------------------------------------------------------------------
# 二分与排序
# --------------------------------------------------------------------------

def _gen_first_position(rng):
    cases = ['1 2\n5\n5\n4\n']
    for n, q, span in ((10, 10, 6), (1000, 1000, 300), (100000, 100000, 20000)):
        values = sorted(_numbers(rng, n, -span, span))
        queries = _numbers(rng, q, -span - 2, span + 2)
        cases.append('%d %d\n%s\n%s\n' % (n, q, _line(values), '\n'.join(str(x) for x in queries)))
    return cases


problem(
    slug='first-position', title='有序数组中第一次出现的位置', difficulty='easy', category='search-sort',
    tags='二分查找',
    description=(
        '给定一个**从小到大排好序**的整数数组（可能有重复元素）和若干次询问。'
        '每次询问给出一个整数 `x`，请回答 `x` 在数组中第一次出现的下标（从 `0` 开始）；'
        '如果数组里没有 `x`，回答 `-1`。'
    ),
    input_format='第一行是两个整数 `n` 和 `q`，分别是数组长度和询问次数。\n第二行是 `n` 个整数。\n接下来 `q` 行，每行一个整数 `x`。',
    output_format='对每次询问输出一行答案。',
    constraints='- `1 ≤ n, q ≤ 10^5`\n- 所有数的绝对值不超过 `10^9`',
    hint='二分查找「第一个大于等于 `x` 的位置」，再检查那个位置上是不是 `x`。注意区间开闭和循环终止条件。',
    samples=['6 3\n1 2 2 2 5 9\n2\n9\n4\n', '3 2\n7 7 7\n7\n8\n'],
    generate=_gen_first_position,
    solution='''import sys


def main():
    data = sys.stdin.read().split()
    n, q = int(data[0]), int(data[1])
    a = [int(x) for x in data[2:2 + n]]
    out = []
    for t in range(q):
        x = int(data[2 + n + t])
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            if a[mid] < x:
                lo = mid + 1
            else:
                hi = mid
        out.append(str(lo if lo < n and a[lo] == x else -1))
    print('\\n'.join(out))


main()
''',
)


def _gen_merge_intervals(rng):
    cases = ['1\n5 5\n', '3\n1 2\n3 4\n5 6\n', '3\n1 10\n2 3\n4 5\n']
    for n, span in ((200, 500), (100000, 10 ** 6)):
        lines = []
        for _ in range(n):
            left = rng.randint(0, span)
            lines.append('%d %d' % (left, left + rng.randint(0, max(1, span // (n // 4 + 1)))))
        cases.append('%d\n%s\n' % (n, '\n'.join(lines)))
    return cases


problem(
    slug='merge-intervals', title='合并区间', difficulty='medium', category='search-sort', tags='排序,区间',
    description=(
        '给定 `n` 个闭区间 `[l, r]`，请把所有**有重叠或首尾相接**的区间合并，'
        '输出合并后互不相交的区间。\n\n'
        '例如 `[1, 3]` 和 `[3, 5]` 有公共点 `3`，应当合并成 `[1, 5]`。'
    ),
    input_format='第一行是整数 `n`。\n接下来 `n` 行，每行两个整数 `l` 和 `r`（`l ≤ r`）。',
    output_format='第一行输出合并后的区间个数 `m`。\n接下来 `m` 行，按左端点从小到大输出每个区间的左右端点。',
    constraints='- `1 ≤ n ≤ 10^5`\n- `0 ≤ l ≤ r ≤ 10^9`',
    hint='按左端点排序后扫描一遍：当前区间的左端点不超过上一个合并结果的右端点，就把右端点取较大值；否则开一个新的区间。',
    samples=['4\n1 3\n2 6\n8 10\n15 18\n', '2\n1 4\n4 5\n'],
    generate=_gen_merge_intervals,
    solution='''import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    intervals = sorted((int(data[1 + 2 * i]), int(data[2 + 2 * i])) for i in range(n))
    merged = []
    for left, right in intervals:
        if merged and left <= merged[-1][1]:
            if right > merged[-1][1]:
                merged[-1][1] = right
        else:
            merged.append([left, right])
    print(len(merged))
    print('\\n'.join('%d %d' % (left, right) for left, right in merged))


main()
''',
)


def _gen_kth_largest(rng):
    cases = ['1 1\n42\n', '5 5\n3 1 2 5 4\n', '4 2\n7 7 7 7\n']
    cases.append('1000 500\n%s\n' % _line(_numbers(rng, 1000, -100, 100)))
    cases.append('100000 12345\n%s\n' % _line(_numbers(rng, 100000, -10 ** 9, 10 ** 9)))
    return cases


problem(
    slug='kth-largest', title='第 K 大的数', difficulty='medium', category='search-sort',
    tags='排序,堆,快速选择',
    description=(
        '给定 `n` 个整数，请找出其中第 `k` 大的数。\n\n'
        '这里的「第 `k` 大」是指**排序后**从大到小数的第 `k` 个，重复的数不合并。'
        '例如 `[5, 5, 3]` 中第 `2` 大的数是 `5`。'
    ),
    input_format='第一行是两个整数 `n` 和 `k`。\n第二行是 `n` 个整数。',
    output_format='输出一个整数，即第 `k` 大的数。',
    constraints='- `1 ≤ k ≤ n ≤ 10^5`\n- 元素的绝对值不超过 `10^9`',
    hint='最直接的是排序。更进一步：维护一个大小为 `k` 的小根堆，或者用快速选择做到平均 O(n)。',
    samples=['6 2\n3 2 1 5 6 4\n', '3 2\n5 5 3\n'],
    generate=_gen_kth_largest,
    solution='''import heapq
import sys


def main():
    data = sys.stdin.read().split()
    n, k = int(data[0]), int(data[1])
    print(heapq.nlargest(k, (int(x) for x in data[2:2 + n]))[-1])


main()
''',
)


# --------------------------------------------------------------------------
# 动态规划
# --------------------------------------------------------------------------

problem(
    slug='climbing-stairs', title='爬楼梯', difficulty='easy', category='dp', tags='动态规划,递推',
    description='一段楼梯共有 `n` 级台阶，每次可以向上走 `1` 级或 `2` 级。请问走到顶一共有多少种不同的走法？',
    input_format='输入一个整数 `n`。',
    output_format='输出一个整数，表示不同走法的数量。',
    constraints='- `1 ≤ n ≤ 90`（答案不超过 64 位有符号整数的范围）',
    hint='走到第 `i` 级的最后一步要么来自第 `i-1` 级，要么来自第 `i-2` 级：`f(i) = f(i-1) + f(i-2)`。',
    samples=['2\n', '5\n'],
    generate=lambda rng: ['1\n', '3\n', '10\n', '45\n', '90\n'],
    solution='''import sys


def main():
    n = int(sys.stdin.read())
    a, b = 1, 1
    for _ in range(n):
        a, b = b, a + b
    print(a)


main()
''',
)


def _gen_max_subarray(rng):
    cases = ['1\n-5\n', '4\n-3 -1 -4 -2\n', '5\n1 2 3 4 5\n']
    cases.append('1000\n%s\n' % _line(_numbers(rng, 1000, -100, 100)))
    cases.append('100000\n%s\n' % _line(_numbers(rng, 100000, -10 ** 4, 10 ** 4)))
    return cases


problem(
    slug='max-subarray-sum', title='最大子数组和', difficulty='easy', category='dp', tags='动态规划,数组',
    description='给定一个整数数组，请找出一个**非空的连续子数组**，使它的元素之和最大，并输出这个最大和。',
    input_format='第一行是整数 `n`。\n第二行是 `n` 个整数。',
    output_format='输出一个整数，表示最大的子数组和。',
    constraints='- `1 ≤ n ≤ 10^5`\n- 元素的绝对值不超过 `10^4`',
    hint='设 `f(i)` 是以第 `i` 个元素结尾的最大子数组和：`f(i) = max(a[i], f(i-1) + a[i])`。全是负数时答案是最大的那个元素。',
    samples=['9\n-2 1 -3 4 -1 2 1 -5 4\n', '3\n-1 -2 -3\n'],
    generate=_gen_max_subarray,
    solution='''import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    best = current = int(data[1])
    for i in range(2, n + 1):
        x = int(data[i])
        current = x if current < 0 else current + x
        if current > best:
            best = current
    print(best)


main()
''',
)


def _gen_coin_change(rng):
    cases = ['1 0\n5\n', '1 3\n2\n', '3 7\n2 4 6\n', '4 9999\n1 5 10 25\n']
    coins = sorted(rng.sample(range(2, 500), 12))
    cases.append('12 10000\n%s\n' % _line(coins))
    cases.append('3 9973\n%s\n' % _line([7, 13, 4999]))
    return cases


problem(
    slug='coin-change', title='零钱兑换', difficulty='medium', category='dp', tags='动态规划,完全背包',
    description=(
        '有 `n` 种面值的硬币，每种硬币的数量不限。请用**最少的硬币个数**凑出总金额 `amount`。'
        '如果无论如何都凑不出来，输出 `-1`。'
    ),
    input_format='第一行是两个整数 `n` 和 `amount`。\n第二行是 `n` 个互不相同的正整数，表示硬币的面值。',
    output_format='输出一个整数，表示最少的硬币个数；无法凑出时输出 `-1`。',
    constraints='- `1 ≤ n ≤ 12`\n- `0 ≤ amount ≤ 10^4`\n- 面值不超过 `10^4`',
    hint='`f(x)` 表示凑出金额 `x` 的最少硬币数：`f(x) = min(f(x - c) + 1)`，其中 `c` 取遍所有面值。贪心（每次取最大面值）并不总是对的。',
    samples=['3 11\n1 2 5\n', '1 3\n2\n'],
    generate=_gen_coin_change,
    solution='''import sys


def main():
    data = sys.stdin.read().split()
    n, amount = int(data[0]), int(data[1])
    coins = [int(x) for x in data[2:2 + n]]
    inf = float('inf')
    best = [0] + [inf] * amount
    for value in range(1, amount + 1):
        for coin in coins:
            if coin <= value and best[value - coin] + 1 < best[value]:
                best[value] = best[value - coin] + 1
    print(-1 if best[amount] == inf else best[amount])


main()
''',
)


def _gen_lis(rng):
    cases = ['1\n10\n', '5\n5 4 3 2 1\n', '6\n2 2 2 2 2 2\n']
    cases.append('1000\n%s\n' % _line(_numbers(rng, 1000, 1, 1000)))
    cases.append('100000\n%s\n' % _line(_numbers(rng, 100000, -10 ** 9, 10 ** 9)))
    cases.append('100000\n%s\n' % _line(range(1, 100001)))
    return cases


problem(
    slug='longest-increasing-subsequence', title='最长上升子序列', difficulty='medium', category='dp',
    tags='动态规划,二分查找',
    description=(
        '给定一个整数数组，请求出它的最长**严格上升**子序列的长度。\n\n'
        '子序列是从数组中删掉若干个（也可以不删）元素后，剩下的元素按原来的顺序组成的序列。'
    ),
    input_format='第一行是整数 `n`。\n第二行是 `n` 个整数。',
    output_format='输出一个整数，表示最长严格上升子序列的长度。',
    constraints='- `1 ≤ n ≤ 10^5`\n- 元素的绝对值不超过 `10^9`',
    hint='O(n²) 的动态规划在这个数据范围下会超时。维护数组 `tails`：`tails[k]` 是长度为 `k+1` 的上升子序列的最小结尾，每来一个数用二分更新它。',
    samples=['8\n10 9 2 5 3 7 101 18\n', '4\n7 7 7 7\n'],
    generate=_gen_lis,
    solution='''import sys
from bisect import bisect_left


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    tails = []
    for i in range(1, n + 1):
        x = int(data[i])
        position = bisect_left(tails, x)
        if position == len(tails):
            tails.append(x)
        else:
            tails[position] = x
    print(len(tails))


main()
''',
)


def _gen_edit_distance(rng):
    cases = ['a\na\n', 'abc\nxyz\n', 'a\nabcdefgh\n']
    cases.append('%s\n%s\n' % (_word(rng, 200, 200, 'abcd'), _word(rng, 180, 180, 'abcd')))
    base = _word(rng, 700, 700)
    mutated = list(base)
    for _ in range(60):
        position = rng.randrange(len(mutated))
        roll = rng.random()
        if roll < 0.34:
            del mutated[position]
        elif roll < 0.67:
            mutated.insert(position, rng.choice(string.ascii_lowercase))
        else:
            mutated[position] = rng.choice(string.ascii_lowercase)
    cases.append('%s\n%s\n' % (base, ''.join(mutated)))
    return cases


problem(
    slug='edit-distance', title='编辑距离', difficulty='hard', category='dp', tags='动态规划,字符串',
    time_limit=2000,
    description=(
        '给定两个字符串 `a` 和 `b`。每次操作可以对 `a` 做下面三件事之一：\n\n'
        '- 插入一个字符；\n- 删除一个字符；\n- 把一个字符替换成另一个字符。\n\n'
        '请求出把 `a` 变成 `b` 最少需要多少次操作。'
    ),
    input_format='输入共两行，分别是字符串 `a` 和 `b`，只包含小写字母。',
    output_format='输出一个整数，表示最少的操作次数。',
    constraints='- 两个字符串的长度都在 `1` 到 `800` 之间',
    hint='`f(i, j)` 表示 `a` 的前 `i` 个字符变成 `b` 的前 `j` 个字符的最少操作数；末尾字符相同时等于 `f(i-1, j-1)`，否则取插入、删除、替换三种情况的最小值再加一。可以只保留上一行来节省空间。',
    samples=['horse\nros\n', 'intention\nexecution\n'],
    generate=_gen_edit_distance,
    solution='''import sys


def main():
    a, b = sys.stdin.read().split()
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        ca = a[i - 1]
        for j in range(1, len(b) + 1):
            if ca == b[j - 1]:
                current[j] = previous[j - 1]
            else:
                best = previous[j - 1]
                if previous[j] < best:
                    best = previous[j]
                if current[j - 1] < best:
                    best = current[j - 1]
                current[j] = best + 1
        previous = current
    print(previous[len(b)])


main()
''',
)


# --------------------------------------------------------------------------
# 搜索与图
# --------------------------------------------------------------------------

def _grid(rng, rows, columns, density):
    return [''.join('1' if rng.random() < density else '0' for _ in range(columns)) for _ in range(rows)]


def _gen_islands(rng):
    cases = ['1 1\n0\n', '1 1\n1\n', '2 2\n10\n01\n']
    for rows, columns, density in ((30, 40, 0.4), (200, 200, 0.55), (200, 200, 0.999)):
        cases.append('%d %d\n%s\n' % (rows, columns, '\n'.join(_grid(rng, rows, columns, density))))
    return cases


problem(
    slug='number-of-islands', title='岛屿数量', difficulty='medium', category='graph',
    tags='深度优先搜索,广度优先搜索,并查集',
    description=(
        '给定一张 `n × m` 的地图，`1` 表示陆地，`0` 表示水。上下左右相邻的陆地连成一座岛屿'
        '（斜着相邻不算）。请统计地图上一共有多少座岛屿。'
    ),
    input_format='第一行是两个整数 `n` 和 `m`。\n接下来 `n` 行，每行是一个长度为 `m` 的 `01` 字符串。',
    output_format='输出一个整数，表示岛屿的数量。',
    constraints='- `1 ≤ n, m ≤ 200`',
    hint='遍历每个格子，遇到没访问过的陆地就计数加一，并从它出发把整座岛标记掉。地图可能整片都是陆地，递归写法要小心栈溢出，建议用显式的栈或队列。',
    samples=['4 5\n11000\n11000\n00100\n00011\n', '3 3\n101\n010\n101\n'],
    generate=_gen_islands,
    solution='''import sys


def main():
    data = sys.stdin.read().split()
    n, m = int(data[0]), int(data[1])
    grid = [list(row) for row in data[2:2 + n]]
    islands = 0
    for i in range(n):
        for j in range(m):
            if grid[i][j] != '1':
                continue
            islands += 1
            grid[i][j] = '0'
            stack = [(i, j)]
            while stack:
                x, y = stack.pop()
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < n and 0 <= ny < m and grid[nx][ny] == '1':
                        grid[nx][ny] = '0'
                        stack.append((nx, ny))
    print(islands)


main()
''',
)


def _gen_maze(rng):
    def maze(rows, columns, density):
        cells = [['#' if rng.random() < density else '.' for _ in range(columns)] for _ in range(rows)]
        (sx, sy), (tx, ty) = rng.sample([(x, y) for x in range(rows) for y in range(columns)], 2)
        cells[sx][sy], cells[tx][ty] = 'S', 'T'
        return '%d %d\n%s\n' % (rows, columns, '\n'.join(''.join(row) for row in cells))

    cases = ['1 2\nST\n', '1 3\nS#T\n', maze(10, 10, 0.2), maze(60, 80, 0.3), maze(200, 200, 0.25),
             maze(200, 200, 0.0)]
    return cases


problem(
    slug='maze-shortest-path', title='迷宫最短路', difficulty='medium', category='graph',
    tags='广度优先搜索,最短路',
    description=(
        '给定一张 `n × m` 的迷宫地图：`.` 是空地，`#` 是墙，`S` 是起点，`T` 是终点。'
        '每一步可以向上下左右移动一格，不能穿墙，也不能走出地图。\n\n'
        '请求出从起点到终点最少需要多少步；如果无法到达，输出 `-1`。'
    ),
    input_format='第一行是两个整数 `n` 和 `m`。\n接下来 `n` 行，每行是一个长度为 `m` 的字符串。地图中恰好有一个 `S` 和一个 `T`。',
    output_format='输出一个整数，表示最少步数，或者 `-1`。',
    constraints='- `1 ≤ n, m ≤ 200`',
    hint='每一步的代价都相同，用广度优先搜索：第一次到达终点时的层数就是最短步数。',
    samples=['3 4\nS..#\n.#..\n...T\n', '2 3\nS#T\n.#.\n'],
    generate=_gen_maze,
    solution='''import sys
from collections import deque


def main():
    data = sys.stdin.read().split()
    n, m = int(data[0]), int(data[1])
    grid = data[2:2 + n]
    start = target = None
    for i in range(n):
        for j in range(m):
            if grid[i][j] == 'S':
                start = (i, j)
            elif grid[i][j] == 'T':
                target = (i, j)
    distance = [[-1] * m for _ in range(n)]
    distance[start[0]][start[1]] = 0
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        if (x, y) == target:
            break
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < n and 0 <= ny < m and grid[nx][ny] != '#' and distance[nx][ny] < 0:
                distance[nx][ny] = distance[x][y] + 1
                queue.append((nx, ny))
    print(distance[target[0]][target[1]])


main()
''',
)


def _gen_dijkstra(rng):
    cases = ['1 0\n', '2 1\n1 2 5\n', '3 1\n1 2 4\n']
    for n, m in ((50, 200), (2000, 20000), (50000, 100000)):
        edges = []
        for node in range(2, n + 1):
            if rng.random() < 0.9:
                edges.append((rng.randint(max(1, node - 50), node - 1), node, rng.randint(1, 10 ** 6)))
        while len(edges) < m:
            edges.append((rng.randint(1, n), rng.randint(1, n), rng.randint(1, 10 ** 6)))
        rng.shuffle(edges)
        cases.append('%d %d\n%s\n' % (n, len(edges), '\n'.join('%d %d %d' % edge for edge in edges)))
    return cases


problem(
    slug='shortest-path', title='带权图的最短路径', difficulty='hard', category='graph',
    tags='最短路,Dijkstra,堆', time_limit=2000,
    description=(
        '给定一个有 `n` 个点、`m` 条边的**无向**图，每条边有一个正的长度。'
        '请求出从 `1` 号点到 `n` 号点的最短路径长度；如果到不了，输出 `-1`。\n\n'
        '图中可能有重边和自环。'
    ),
    input_format='第一行是两个整数 `n` 和 `m`。\n接下来 `m` 行，每行三个整数 `u`、`v`、`w`，表示 `u` 和 `v` 之间有一条长度为 `w` 的边。',
    output_format='输出一个整数，表示最短路径的长度，或者 `-1`。',
    constraints='- `1 ≤ n ≤ 5 × 10^4`\n- `0 ≤ m ≤ 10^5`\n- `1 ≤ w ≤ 10^6`',
    hint='边权为正，用堆优化的 Dijkstra：每次取出距离最小且尚未确定的点，用它松弛相邻的点。',
    samples=['4 5\n1 2 1\n2 3 2\n1 3 5\n3 4 1\n2 4 7\n', '3 1\n1 2 4\n'],
    generate=_gen_dijkstra,
    solution='''import heapq
import sys


def main():
    data = sys.stdin.read().split()
    n, m = int(data[0]), int(data[1])
    graph = [[] for _ in range(n + 1)]
    for i in range(m):
        u, v, w = int(data[2 + 3 * i]), int(data[3 + 3 * i]), int(data[4 + 3 * i])
        graph[u].append((v, w))
        graph[v].append((u, w))
    inf = float('inf')
    distance = [inf] * (n + 1)
    distance[1] = 0
    heap = [(0, 1)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > distance[u]:
            continue
        if u == n:
            break
        for v, w in graph[u]:
            if d + w < distance[v]:
                distance[v] = d + w
                heapq.heappush(heap, (d + w, v))
    print(-1 if distance[n] == inf else distance[n])


main()
''',
)


def _gen_courses(rng):
    cases = ['1 0\n', '2 2\n0 1\n1 0\n', '3 1\n1 1\n']
    for n, m, cyclic in ((100, 300, False), (100, 300, True), (20000, 60000, False), (20000, 60000, True)):
        order = list(range(n))
        rng.shuffle(order)
        edges = []
        for _ in range(m):
            a, b = sorted(rng.sample(range(n), 2))
            edges.append((order[b], order[a]))     # order[a] before order[b]
        if cyclic:
            a, b, c = rng.sample(range(n), 3)
            edges += [(a, b), (b, c), (c, a)]
        rng.shuffle(edges)
        cases.append('%d %d\n%s\n' % (n, len(edges), '\n'.join('%d %d' % edge for edge in edges)))
    return cases


problem(
    slug='course-schedule', title='课程安排', difficulty='medium', category='graph',
    tags='拓扑排序,有向图,环检测',
    description=(
        '这学期一共要修 `n` 门课，编号从 `0` 到 `n - 1`。有些课有先修要求：'
        '一条要求 `a b` 表示想修课程 `a`，必须先修完课程 `b`。\n\n'
        '请判断是否存在一种顺序，可以把所有课程修完。'
    ),
    input_format='第一行是两个整数 `n` 和 `m`，分别是课程数和先修要求的条数。\n接下来 `m` 行，每行两个整数 `a` 和 `b`。',
    output_format='可以修完输出 `YES`，否则输出 `NO`。',
    constraints='- `1 ≤ n ≤ 2 × 10^4`\n- `0 ≤ m ≤ 10^5`',
    hint='把先修关系看成有向边，问题就是判断图里有没有环。不断拿掉入度为 0 的点（Kahn 算法），最后能拿完所有点就没有环。',
    samples=['2 1\n1 0\n', '2 2\n1 0\n0 1\n'],
    generate=_gen_courses,
    solution='''import sys
from collections import deque


def main():
    data = sys.stdin.read().split()
    n, m = int(data[0]), int(data[1])
    graph = [[] for _ in range(n)]
    indegree = [0] * n
    for i in range(m):
        a, b = int(data[2 + 2 * i]), int(data[3 + 2 * i])
        graph[b].append(a)
        indegree[a] += 1
    queue = deque(i for i in range(n) if indegree[i] == 0)
    done = 0
    while queue:
        u = queue.popleft()
        done += 1
        for v in graph[u]:
            indegree[v] -= 1
            if indegree[v] == 0:
                queue.append(v)
    print('YES' if done == n else 'NO')


main()
''',
)


# --------------------------------------------------------------------------
# 数学与模拟
# --------------------------------------------------------------------------

problem(
    slug='gcd-and-lcm', title='最大公约数与最小公倍数', difficulty='easy', category='math-sim',
    tags='数学,辗转相除',
    description='给定两个正整数 `a` 和 `b`，请求出它们的最大公约数和最小公倍数。',
    input_format='输入一行，两个正整数 `a` 和 `b`。',
    output_format='输出一行，两个整数，分别是最大公约数和最小公倍数。',
    constraints='- `1 ≤ a, b ≤ 10^9`',
    hint='辗转相除：`gcd(a, b) = gcd(b, a mod b)`。最小公倍数是 `a / gcd × b`，先除后乘可以避免中间结果溢出（结果仍需要 64 位整数）。',
    samples=['12 18\n', '7 5\n'],
    generate=lambda rng: ['1 1\n', '1000000000 1000000000\n', '999999937 999999929\n', '1000000000 2\n',
                          '%d %d\n' % (rng.randint(1, 10 ** 9), rng.randint(1, 10 ** 9)),
                          '%d %d\n' % (2 ** 20 * 3, 2 ** 15 * 9)],
    solution='''import sys


def main():
    a, b = map(int, sys.stdin.read().split())
    x, y = a, b
    while y:
        x, y = y, x % y
    print(x, a // x * b)


main()
''',
)


problem(
    slug='count-primes', title='统计质数', difficulty='easy', category='math-sim', tags='数学,筛法',
    description='给定一个整数 `n`，请统计不超过 `n` 的质数有多少个。',
    input_format='输入一个整数 `n`。',
    output_format='输出一个整数，表示不超过 `n` 的质数个数。',
    constraints='- `1 ≤ n ≤ 10^6`',
    hint='埃拉托斯特尼筛法：从 2 开始，把每个质数的倍数都划掉，剩下的就是质数。',
    samples=['10\n', '1\n'],
    generate=lambda rng: ['2\n', '3\n', '100\n', '99991\n', '1000000\n'],
    solution='''import sys


def main():
    n = int(sys.stdin.read())
    if n < 2:
        print(0)
        return
    sieve = bytearray([1]) * (n + 1)
    sieve[0] = sieve[1] = 0
    i = 2
    while i * i <= n:
        if sieve[i]:
            sieve[i * i::i] = bytearray(len(range(i * i, n + 1, i)))
        i += 1
    print(sum(sieve))


main()
''',
)


def _matrix(rng, rows, columns, low=-99, high=99):
    return '\n'.join(_line(_numbers(rng, columns, low, high)) for _ in range(rows))


problem(
    slug='rotate-matrix', title='旋转矩阵', difficulty='easy', category='math-sim', tags='矩阵,模拟',
    description='给定一个 `n × n` 的整数矩阵，请把它**顺时针旋转 90 度**后输出。',
    input_format='第一行是整数 `n`。\n接下来 `n` 行，每行 `n` 个整数。',
    output_format='输出 `n` 行，每行 `n` 个整数，表示旋转后的矩阵。',
    constraints='- `1 ≤ n ≤ 200`\n- 元素的绝对值不超过 `1000`',
    hint='旋转后第 `i` 行第 `j` 列的元素来自原矩阵第 `n-1-j` 行第 `i` 列。想原地完成的话：先沿主对角线翻转，再把每一行左右翻转。',
    samples=['3\n1 2 3\n4 5 6\n7 8 9\n', '2\n1 2\n3 4\n'],
    generate=lambda rng: ['1\n5\n'] + ['%d\n%s\n' % (n, _matrix(rng, n, n)) for n in (4, 31, 200)],
    solution='''import sys


def main():
    data = sys.stdin.read().split()
    n = int(data[0])
    a = [data[1 + i * n:1 + (i + 1) * n] for i in range(n)]
    for i in range(n):
        print(' '.join(a[n - 1 - j][i] for j in range(n)))


main()
''',
)


problem(
    slug='spiral-order', title='螺旋遍历矩阵', difficulty='medium', category='math-sim', tags='矩阵,模拟',
    description=(
        '给定一个 `n × m` 的整数矩阵，请从左上角出发，按**顺时针螺旋**的顺序（先向右，再向下、向左、向上，一圈一圈往里）'
        '输出矩阵中的所有元素。'
    ),
    input_format='第一行是两个整数 `n` 和 `m`。\n接下来 `n` 行，每行 `m` 个整数。',
    output_format='输出一行，共 `n × m` 个整数，用空格分隔。',
    constraints='- `1 ≤ n, m ≤ 200`\n- 元素的绝对值不超过 `1000`',
    hint='维护上、下、左、右四个边界，每走完一条边就把对应的边界向里收一格；注意只剩一行或一列时不要重复输出。',
    samples=['3 3\n1 2 3\n4 5 6\n7 8 9\n', '2 4\n1 2 3 4\n5 6 7 8\n'],
    generate=lambda rng: ['1 1\n9\n', '1 5\n1 2 3 4 5\n', '4 1\n1\n2\n3\n4\n'] + [
        '%d %d\n%s\n' % (n, m, _matrix(rng, n, m)) for n, m in ((5, 8), (200, 37), (200, 200))],
    solution='''import sys


def main():
    data = sys.stdin.read().split()
    n, m = int(data[0]), int(data[1])
    a = [data[2 + i * m:2 + (i + 1) * m] for i in range(n)]
    top, bottom, left, right = 0, n - 1, 0, m - 1
    out = []
    while top <= bottom and left <= right:
        for j in range(left, right + 1):
            out.append(a[top][j])
        for i in range(top + 1, bottom + 1):
            out.append(a[i][right])
        if top < bottom:
            for j in range(right - 1, left - 1, -1):
                out.append(a[bottom][j])
        if left < right:
            for i in range(bottom - 1, top, -1):
                out.append(a[i][left])
        top, bottom, left, right = top + 1, bottom - 1, left + 1, right - 1
    print(' '.join(out))


main()
''',
)


problem(
    slug='permutations', title='全排列', difficulty='medium', category='math-sim', tags='回溯,递归',
    description='给定一个整数 `n`，请按**字典序从小到大**输出 `1` 到 `n` 的所有排列。',
    input_format='输入一个整数 `n`。',
    output_format='输出 `n!` 行，每行一个排列，数字之间用空格分隔。',
    constraints='- `1 ≤ n ≤ 8`',
    hint='回溯：依次决定每个位置放哪个数，用一个标记数组记录哪些数已经用过；从小到大枚举就自然得到字典序。',
    samples=['2\n', '3\n'],
    generate=lambda rng: ['1\n', '4\n', '6\n', '8\n'],
    solution='''import sys
from itertools import permutations


def main():
    n = int(sys.stdin.read())
    out = [' '.join(map(str, p)) for p in permutations(range(1, n + 1))]
    print('\\n'.join(out))


main()
''',
)


problem(
    slug='n-queens', title='N 皇后', difficulty='hard', category='math-sim', tags='回溯,位运算',
    time_limit=2000,
    description=(
        '在 `n × n` 的棋盘上放 `n` 个皇后，要求任意两个皇后都不在同一行、同一列或同一条斜线上。'
        '请问一共有多少种不同的放法？'
    ),
    input_format='输入一个整数 `n`。',
    output_format='输出一个整数，表示放法的总数。',
    constraints='- `1 ≤ n ≤ 10`',
    hint='一行一行地放：用三个集合（或三个二进制数）分别记录已被占用的列、主对角线和副对角线，冲突就回溯。',
    samples=['4\n', '1\n'],
    generate=lambda rng: ['2\n', '3\n', '6\n', '8\n', '10\n'],
    solution='''import sys


def main():
    n = int(sys.stdin.read())
    full = (1 << n) - 1

    def place(columns, left, right):
        if columns == full:
            return 1
        count = 0
        free = full & ~(columns | left | right)
        while free:
            bit = free & -free
            free -= bit
            count += place(columns | bit, ((left | bit) << 1) & full, (right | bit) >> 1)
        return count

    print(place(0, 0, 0))


main()
''',
)


# --------------------------------------------------------------------------
# 数据结构设计
# --------------------------------------------------------------------------

def _gen_lru(rng):
    cases = ['1 3\nput 1 1\nput 2 2\nget 1\n']
    for capacity, count, keys in ((2, 30, 5), (50, 3000, 120), (1000, 100000, 3000)):
        lines = []
        for _ in range(count):
            if rng.random() < 0.5:
                lines.append('put %d %d' % (rng.randint(1, keys), rng.randint(0, 10 ** 9)))
            else:
                lines.append('get %d' % rng.randint(1, keys))
        if not any(line.startswith('get') for line in lines):
            lines.append('get 1')
        cases.append('%d %d\n%s\n' % (capacity, len(lines), '\n'.join(lines)))
    return cases


problem(
    slug='lru-cache', title='LRU 缓存', difficulty='medium', category='design', tags='设计,哈希表,双向链表',
    description=(
        '实现一个容量为 `capacity` 的 LRU（最近最少使用）缓存，支持两种操作：\n\n'
        '- `get key`：如果 `key` 在缓存中，输出它的值，并把它标记为「最近使用」；否则输出 `-1`。\n'
        '- `put key value`：写入或更新 `key` 的值，并把它标记为「最近使用」。'
        '如果写入后缓存中的键超过了 `capacity` 个，淘汰**最久没有被使用**的那个键。\n\n'
        '要求两种操作的平均时间复杂度都是 O(1)。'
    ),
    input_format='第一行是两个整数 `capacity` 和 `q`，分别是缓存容量和操作个数。\n接下来 `q` 行，每行一个操作。',
    output_format='对每个 `get` 操作输出一行结果。',
    constraints='- `1 ≤ capacity ≤ 1000`\n- `1 ≤ q ≤ 10^5`\n- `1 ≤ key ≤ 10^4`，`0 ≤ value ≤ 10^9`\n- 至少有一个 `get` 操作',
    hint='哈希表负责 O(1) 找到节点，双向链表负责维护使用顺序：访问或更新时把节点移到表头，淘汰时删掉表尾。',
    samples=[
        '2 9\nput 1 1\nput 2 2\nget 1\nput 3 3\nget 2\nput 4 4\nget 1\nget 3\nget 4\n',
        '1 4\nput 5 50\nput 5 51\nget 5\nget 6\n',
    ],
    generate=_gen_lru,
    solution='''import sys
from collections import OrderedDict


def main():
    lines = sys.stdin.read().split('\\n')
    capacity, q = map(int, lines[0].split())
    cache = OrderedDict()
    out = []
    for line in lines[1:1 + q]:
        parts = line.split()
        key = int(parts[1])
        if parts[0] == 'get':
            if key in cache:
                cache.move_to_end(key)
                out.append(str(cache[key]))
            else:
                out.append('-1')
        else:
            if key in cache:
                cache.move_to_end(key)
            cache[key] = int(parts[2])
            if len(cache) > capacity:
                cache.popitem(last=False)
    print('\\n'.join(out))


main()
''',
)


def build_cases(spec, seed=20261121):
    """[(input, expected_output, is_sample)] for one problem specification."""
    rng = random.Random('%s-%d' % (spec['slug'], seed))
    cases = [(text, run_reference(spec['solution'], text), True) for text in spec['samples']]
    for text in spec['generate'](rng):
        cases.append((text, run_reference(spec['solution'], text), False))
    return cases
