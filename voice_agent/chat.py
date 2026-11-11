"""Talk to the agent in the terminal.

    python chat.py                 # start a conversation
    python chat.py --list-models   # show model names your key can use (if the default model is rejected)

In the chat:  /trace  shows the tool calls and timings so far,  /reset  starts over,  /quit  exits.
Tool calls are printed in [brackets] so you can see what the model decided to do.
"""
import json
import sys

import agent


def main():
    client = agent.get_client()
    if "--list-models" in sys.argv:
        for m in sorted(x.id for x in client.models.list().data):
            print(m)
        return
    a = agent.Agent(client)
    print(f"Model: {a.model}   (type /quit to exit)\n")
    shown = 0
    while True:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/reset":
            a = agent.Agent(client)
            shown = 0
            print("(conversation reset)\n")
            continue
        if text == "/trace":
            print(json.dumps(a.trace, indent=2), "\n")
            continue
        reply = a.respond(text)
        for t in a.trace[-1]["steps"]:
            print(f"   [{t['tool']} {t['args']} -> {t['status']}  {t['ms']} ms]")
        print(f"Agent: {reply}")
        print(f"   (model {a.trace[-1]['llm_ms']:.0f} ms, tools {a.trace[-1]['tool_ms']:.0f} ms)\n")


if __name__ == "__main__":
    main()
