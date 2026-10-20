"""A tiny arithmetic expression evaluator.

Supports non-negative integers, + - * /, parentheses, and unary minus such as "-3" or
"2 * -(1 + 1)". Normal precedence applies: * and / before + and -. Operators with the same
precedence are evaluated left to right, so "10 - 3 - 2" is 5 and "8 / 4 / 2" is 1.0.
Division always returns a float. Invalid input raises ValueError.
"""


def tokenize(text):
    """Split text into number and operator tokens, e.g. "12+(3)" -> ["12", "+", "(", "3", ")"]."""
    tokens = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch.isdigit():
            start = i
            while i < len(text) and text[i].isdigit():
                i += 1
            tokens.append(text[start:i])
        elif ch in "+-*/()":
            tokens.append(ch)
            i += 1
        else:
            raise ValueError(f"unexpected character {ch!r}")
    return tokens


class Parser:
    """Recursive descent parser.

    expression := term (("+" | "-") term)*
    term       := factor (("*" | "/") factor)*
    factor     := NUMBER | "(" expression ")" | "-" factor
    """

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self):
        token = self.peek()
        if token is None:
            raise ValueError("unexpected end of input")
        self.pos += 1
        return token

    def parse(self):
        value = self.expression()
        if self.peek() is not None:
            raise ValueError(f"unexpected token {self.peek()!r}")
        return value

    def expression(self):
        left = self.term()
        while self.peek() in ("+", "-"):
            op = self.take()
            right = self.term()
            left = left + right if op == "+" else left - right
        return left

    def term(self):
        left = self.factor()
        while self.peek() in ("*", "/"):
            op = self.take()
            right = self.factor()
            left = left * right if op == "*" else left / right
        return left

    def factor(self):
        token = self.take()
        if token == "(":
            value = self.expression()
            if self.take() != ")":
                raise ValueError("expected ')'")
            return value
        if token == "-":
            return -self.factor()
        if token.isdigit():
            return int(token)
        raise ValueError(f"unexpected token {token!r}")


def evaluate(text):
    return Parser(tokenize(text)).parse()
