"""Helpers shared by dj-turso's own test suites."""


# The exception, then each explicit cause it was raised from.
def get_cause_chain(exception: BaseException) -> list[type[BaseException]]:
    chain: list[type[BaseException]] = []
    current: BaseException | None = exception
    while current is not None:
        chain.append(type(current))
        current = current.__cause__
    return chain
