from agent_runtime.agent import Agent

agent = Agent(
    skills_path="./skills"
)

task = "Evaluate the short_story.md assignment"

agent.run(task)
