"""The five agents. Each has its own goals, constraints, memory and message history.

An agent decides by asking the language model (llm.py). If the model is unavailable, times out or
returns something invalid, a simple rule-based fallback answers instead and the message is labelled
source="fallback-rules" so judges can see it.
"""
import json

import llm

SPECS = {
    "commander": {
        "label": "Commander",
        "mission": "Coordinate the council, keep the numbers honest and approve only a valid, fair plan.",
        "goals": "Colony-wide safety. Find a plan every department can accept. Make the severe loss fair, "
                 "decide whether short-term habitat safety or long-term repair capacity matters more.",
        "constraints": "You cannot bypass the validator, the risk limit, the return commitments or the four votes.",
    },
    "life_support": {
        "label": "Life Support",
        "mission": "Seal the habitat and stabilize the air.",
        "goals": "Protect Oxygen and continuous Power. You strongly resist Sacrifice mode (L3) because the living "
                 "zone would close; you only accept it when two fair, valid return commitments are attached.",
        "constraints": "Choose one published mode (L1, L2, L3). You cannot trim resources inside a package.",
    },
    "medical": {
        "label": "Medical",
        "mission": "Stabilize and treat the injured crew (six injured, 42 colonists).",
        "goals": "Protect Power, Oxygen and Bandwidth for patients. You refuse Sacrifice mode (M3) unless there is "
                 "no safer option; a delayed surgery risks a life.",
        "constraints": "Choose one published mode (M1, M2, M3). You cannot trim resources inside a package.",
    },
    "food": {
        "label": "Food Production",
        "mission": "Protect the crop cycle and cooling loop.",
        "goals": "Protect Water and Power for the greenhouse. You resist Sacrifice mode (F3) because a crop bay is lost.",
        "constraints": "Choose one published mode (F1, F2, F3). You cannot trim resources inside a package.",
    },
    "engineering": {
        "label": "Engineering",
        "mission": "Repair power generation and critical systems.",
        "goals": "Protect Robot time and Bandwidth so repairs can continue. You value long-term repair capacity and "
                 "resist Sacrifice mode (E3), but you will help other departments with Robot time if fairly asked.",
        "constraints": "Choose one published mode (E1, E2, E3). You cannot trim resources inside a package.",
    },
}


class Agent:
    def __init__(self, key, runtime):
        spec = SPECS[key]
        self.key = key
        self.label = spec["label"]
        self.spec = spec
        self.llm_timeout = runtime.get("llm_timeout_s", 25)
        self.memory = []          # what this agent has seen, oldest first
        self.stats = {"llm_calls": 0, "fallbacks": 0}

    def system_prompt(self):
        s = self.spec
        return (f"You are the {self.label} agent in a five-agent council (Commander, Life Support, Medical, Food "
                f"Production, Engineering) negotiating over one shared resource pool after a micrometeorite storm "
                f"on the Ares Mars colony.\nMission: {s['mission']}\nGoals: {s['goals']}\n"
                f"Constraints: {s['constraints']}\nBe concise, specific and use the numbers you are given. "
                f"Answer with ONE JSON object only, no other text.")

    def observe(self, msg):
        line = f"R{msg['round']} v{msg['plan_version']} {msg['sender']} [{msg['type']}]: {msg['content'][:220]}"
        self.memory.append(line)
        self.memory = self.memory[-40:]

    def ask(self, kind, context, fields, validate, fallback):
        """Ask the model. Returns (data, source, note). source is 'llm:<provider:model>' or 'fallback-rules'."""
        prompt = (f"TASK_KIND: {kind}\nWhat you remember so far:\n" + "\n".join(self.memory[-12:]) +
                  f"\n\nCONTEXT_JSON: {json.dumps(context)}\n\nReturn JSON with exactly these keys: {fields}")
        try:
            self.stats["llm_calls"] += 1
            data, src = llm.call_json(self.system_prompt(), prompt, timeout=self.llm_timeout)
            validate(data)
            return data, f"llm:{src}", None
        except Exception as e:  # LLMError, ValueError from validate, KeyError ...
            self.stats["fallbacks"] += 1
            return fallback(), "fallback-rules", f"{type(e).__name__}: {e}"
