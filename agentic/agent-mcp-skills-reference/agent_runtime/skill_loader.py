import os
import importlib.util

def load_skills(path):

    skills = {}

    for skill_name in os.listdir(path):

        skill_path = os.path.join(path, skill_name)

        if os.path.isdir(skill_path):

            script = os.path.join(skill_path, "run.py")

            if os.path.exists(script):

                spec = importlib.util.spec_from_file_location(skill_name, script)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                skills[skill_name] = {
                    "execute": module.run
                }

    return skills
