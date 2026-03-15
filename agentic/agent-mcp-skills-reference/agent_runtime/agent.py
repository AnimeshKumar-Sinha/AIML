import os
from skill_loader import load_skills
from planner import create_plan

class Agent:

    def __init__(self, skills_path):
        self.skills = load_skills(skills_path)

    def run(self, task):

        print("Task received:", task)

        plan = create_plan(task, self.skills)

        for step in plan:
            skill = self.skills.get(step["skill"])

            if skill:
                skill["execute"](step["input"])
