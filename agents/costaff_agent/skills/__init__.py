"""Skills 載入器：自動掃描本資料夾下所有具備 `SKILL.md` 的子資料夾並載入。

使用方式：
    from skills import load_all_skills
    skills = load_all_skills()  # 取得 Skill 物件清單，可傳入 SkillToolset

每個 Skill 子資料夾必須符合 Agent Skill 規範（https://agentskills.io/specification）。
新增 Skill 時，只要在本資料夾下建立 `<skill-name>/SKILL.md`，即會自動被載入，
無須手動於 `agent.py` 註冊。
"""
from pathlib import Path
from typing import List

from google.adk.skills import load_skill_from_dir

_SKILLS_DIR = Path(__file__).parent


def load_all_skills() -> List:
    """掃描 skills/ 資料夾下所有具備 SKILL.md 的子資料夾並載入為 Skill 物件。

    Returns:
        List: 所有可用 Skill 物件組成的清單，可直接傳入 SkillToolset。
    """
    skills = []
    for child in sorted(_SKILLS_DIR.iterdir()):
        if not child.is_dir():
            continue
        if (child / "SKILL.md").exists():
            skills.append(load_skill_from_dir(child))
    return skills
