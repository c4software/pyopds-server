"""Questions of the command line tools to the person at the terminal: a free answer, a choice in a numbered
list, a confirmation. Without a terminal (a script, `docker exec` without -it), nothing is asked: the
default answer is taken.
"""
import sys


def interactive() -> bool:
    """Whether a person answers at the terminal."""
    return sys.stdin.isatty()


def ask(question: str, default: str = '') -> str:
    """Answer to a question, spaces trimmed (`default` when empty, or without a terminal)."""
    if not interactive():
        return default
    try:
        return input(f'{question} ').strip() or default
    except EOFError:
        return default


def confirm(question: str) -> bool:
    """Yes or no (yes by default, and without a terminal)."""
    return ask(f'{question} [O/n]', 'o').lower() in ('o', 'oui', 'y', 'yes')


def choose(question: str, options: list[str], default: str = '', more: str = '') -> str:
    """Option chosen by its number in a numbered list, or typed (Enter: `default`, marked in the list);
    `more` is printed after the list (options left out…)."""
    for number, option in enumerate(options, 1):
        print(f'  {number:>3}. {option}{"  (par défaut)" if option == default else ""}')
    if more:
        print(f'       {more}')
    answer = ask(question, default)
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1]
    return answer


__all__ = ['ask', 'choose', 'confirm', 'interactive']
