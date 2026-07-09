---
title: SOUL.md Episode 0003 - Test Sound Insertion
date: 2026-07-09
duration_expected: 2-3 minutes
test_objective: Verify [SOUND: X] markers actually insert audio into the mix
---

# SOUL.md Test 0003: Sound Effect Insertion

**[SOUND: theme]** Hal: This episode should start with a theme sound. Did you hear it?

Ada: If you heard audio at the beginning, sound insertion is working.

**[NOTIFICATION]** Vera: Notification sounds should fire when marked.

Hal: Let me add another one mid-dialogue.

**[SOUND: whoosh]** Ada: You should hear a whoosh sound right before this line.

Vera: Multiple sounds in one episode test our concat logic.

**[SOUND: success_chime]** Hal: Success chime should play here.

Ada: If you heard all the sound effects, the insertion system works.

Vera: If any were missing or spoken aloud as metadata, it failed.

**[SOUND: transition]** Hal: Final transition sound ends this test.
