### BioFuzz
The file "BIOFUZZ_STRUCTURE.md" is the detailed guide to executing this project. The file "README.md" describe the general overview. The README.md can be changed to better depict the project overview. **Do not change BIOFUZZ_STRUCTURE.md.**

### Agent - User Interaction
- You are an autonomous agent in an automated pipeline.
- The .agent/USER.md file is where the agent can communicate to the user. Dcisions or suggestions about the project should be recorded here. **DO NOT WAIT FOR A RESPONSE**. If you have a suggestion within the general scope of the project, **execute it without asking** and record the decision in this file. If the suggestion is far beyond the scope of the project, make a suggestion and continue. Again, **NEVER WAIT FOR A RESPONSE**.
- Keep preambles to a single declarative sentence ("I'm scanning the repo and then drafting a minimal fix.") — no approval requests.
- Do not ask for permission.

### Tasking Approach
The phases of this project should be divided into tasks. Track these tasks using the .agent/exec-plans/ directory. Each task has an associated .txt file which will describe the task, the challenges of accomplishing the task, the approaches to complete this task, and, in the case of completed tasks, the approach that successfully completed the task. Track active tasks in the .agent/exec-plans/active/ directory and completed tasks in the .agent/exec-plans/completed/ directory. .agent/PLANS.md should be a brief overview of the active and completed plans.

When a phase is complete, produce a summary describing the overall approach used and challenges overcome in completing the phase. This should be recorded in .agent/PHASES.md. Include a discussion of future work. This should include potential improvements, why they would be improvements, and the challengers of implementing them. The future work section is not necessary.

### Tools
Build any tools that may improve your efficiency and effectiveness in building this project. Store these tools in the .agent/tools directory.

### GitHub
Build the project on a *develop* branch. The completion of a task or phase should be committed to the GitHub repository. Each phase should be built on their own branch before being pushed to the *develop* branch when completed. Upon completion of all phases, the project is complete on the *develop* branch and therefore should be pushed to *main*.