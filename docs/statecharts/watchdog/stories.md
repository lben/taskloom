## 1. After the keeper looks: scheduler is hung, and you were told why

Rule broken: After the keeper looks, a scheduler that was dead or hung is checking again.
Why: The hung scheduler still holds the folder lock, so the new one cannot start.
This is the only way to get here in the model.

0. Start: Before the keeper looks: scheduler is hung
1. The keeper tries to start another scheduler. Now: After the keeper looks: scheduler is hung, and you were told why

With the fix, step 1 (the keeper tries to start another scheduler) takes you to: After the keeper looks: scheduler is checking.
