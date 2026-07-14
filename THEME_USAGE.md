# Podcast Theme Songs

This directory contains theme song audio files for the AI Post Transformers podcast.

## Files

- **theme.mp3** (240 KB, ~9.5 seconds) — Full theme with intro + outro. Use for special/theatrical episodes (SOUL.md, Severance, etc.) where the complete theme should play during the introduction.

- **theme-intro.mp3** (70 KB, ~2.7 seconds) — Intro segment only. Use after the countdown ("3 2 1") in standard episode introductions. Plays before hosts begin dialogue.

- **theme-end.mp3** (144 KB, ~5.6 seconds) — Outro/closer segment. Use at the end of episodes as a musical conclusion before the outro message or episode close.

## Usage by Episode Type

### Standard Research Episodes
- Intro: After countdown, play `theme-intro.mp3`, then hosts begin dialogue
- Outro: At episode end, play `theme-end.mp3` before final message

### Special/Theatrical Episodes (SOUL.md, Severance, etc.)
- Intro: After countdown, play full `theme.mp3` for complete theatrical opening
- Outro: At episode end, play `theme-end.mp3` before final message

## Implementation

Code paths reference these files via:
```python
theme_intro = Path(__file__).parent / "podcast-theme" / "theme-intro.mp3"
theme_end = Path(__file__).parent / "podcast-theme" / "theme-end.mp3"
theme_full = Path(__file__).parent / "podcast-theme" / "theme.mp3"
```

## Checksums

Verify integrity:
```
9717df93c9c502a124aba4725ccca9f529eb25e5e1a0827972628fbf2f67b9f1  theme-end.mp3
0b47e28212fd6c8113a0cd8cef3e57734283aec35563265e4a23579f7b6b5f2e  theme-intro.mp3
e354a3f374a77ff1dc6b94fdb69e7d91ebda2a4b0f8665c349604e07b0b9ec78  theme.mp3
```
