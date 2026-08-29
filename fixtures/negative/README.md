# Negative fixture family

Executable cases live in `tests/test_attacks.py`. They mutate the local
positive slice to reproduce SCENE-002 attack classes:

- missing / tampered artifact
- placeholder hash
- snippet as B
- same-origin dual B
- UNKNOWN origin
- post-freeze source
- EPHEMERAL durability
- export tamper
- invalidated build
