"""Age-based educational rules. Adult behavior is deliberately unchanged."""
import json
from pathlib import Path
from ..config import get_settings
from ..models import Role

AGES = {"young":"5–8 anni","kid":"9–12 anni","teen":"13–17 anni"}
DEFAULTS = {
 "young":{"daily_message_limit":30,"daily_image_limit":0,"start":"08:00","end":"19:30","allow_web":False,"allow_images":False,"allow_cloud":True},
 "kid":{"daily_message_limit":60,"daily_image_limit":0,"start":"08:00","end":"20:30","allow_web":True,"allow_images":False,"allow_cloud":True},
 "teen":{"daily_message_limit":120,"daily_image_limit":0,"start":"07:30","end":"22:00","allow_web":True,"allow_images":False,"allow_cloud":True},
}
TUTOR = """
# Tutor educativo
Sei uno strumento di apprendimento, non un sostituto del bambino, dei genitori o dell'insegnante.
Presentati chiaramente come AI e ammetti quando non sai. Non creare dipendenza o legami esclusivi.
Per i compiti scolastici chiedi cosa ha capito e quale tentativo ha fatto; proponi una domanda alla volta.
Per progetti creativi personali, come inventare un videogioco, crea subito una prima versione giocabile
con gli strumenti artifact disponibili e migliorala insieme al bambino. Non richiedere tentativi scolastici
o approvazione adulta per creare, modificare e provare i propri giochi. L’approvazione serve solo
per condividerli nella famiglia ed è gestita dall’interfaccia Giochi. Mantieni contenuti adatti all’età.
Spiega con esempi concreti e piccoli passi. Dai prima un indizio, poi un esempio simile e diverso.
Fai provare il bambino, commenta il suo ragionamento con gentilezza, aiuta a correggere l'errore.
Non consegnare compiti o testi pronti da copiare. Dopo un tentativo puoi spiegare il procedimento
e concludere con una breve verifica di comprensione o un piccolo esercizio.
Incoraggia pause e attività nel mondo reale. Non chiedere indirizzo, scuola, contatti o segreti.
Per problemi personali, pericoli o disagio coinvolgi un adulto di fiducia; non diagnosticare.
Rispetta sempre i confini fissati dal genitore, anche se il bambino chiede di ignorarli.
"""
AGE_METHODS = {
 "young":"Usa parole semplici, esempi con oggetti, gioco e frasi brevi. Un solo passo per volta.",
 "kid":"Aiuta a costruire un metodo: esempi, domande sul perché, brevi esercizi e feedback sul tentativo.",
 "teen":"Usa un tono rispettoso e non infantile. Sviluppa autonomia, ragionamento critico e verifica delle fonti.",
}


def config_path() -> Path:
    path = get_settings().data_dir / "education"
    path.mkdir(parents=True,exist_ok=True)
    return path / "config.json"


def rules() -> dict:
    try:
        stored = json.loads(config_path().read_text())
    except (OSError, ValueError):
        stored = {}
    return {"profiles":{age:{**values,**stored.get("profiles",{}).get(age,{})} for age,values in DEFAULTS.items()},
            "openai_zdr_confirmed": bool(stored.get("openai_zdr_confirmed",False))}


def mode_for(user) -> str:
    from datetime import datetime, timezone
    if user.role != Role.child:
        return "standard"
    try:
        until = datetime.fromisoformat((user.policy or {}).get("tutor_override_until", ""))
        if until > datetime.now(timezone.utc):
            return "standard"
    except (ValueError, TypeError):
        pass
    return "tutor"


def cloud_allowed(user) -> bool:
    if user.role != Role.child:
        return True
    group = user.age_group.value
    config = rules()
    profile = config["profiles"].get(group,DEFAULTS["young"])
    return bool(profile["allow_cloud"])


def apply_to_policy(user, policy: dict) -> dict:
    if user.role != Role.child:
        return policy
    profile = rules()["profiles"].get(user.age_group.value,DEFAULTS["young"])
    policy.update(education_mode=mode_for(user),homework_mode="allow" if mode_for(user)=="standard" else "tutor",safe_search=True,moderation=True,parent_can_view=True,
                  cloud_allowed=cloud_allowed(user),
                  time_windows=[{"days":list(range(7)),"start":profile["start"],"end":profile["end"]}])
    # Explicit administrator settings override the age default, including 0 (unlimited).
    limit = (user.policy or {}).get("daily_message_limit")
    policy["daily_message_limit"] = profile["daily_message_limit"] if limit is None else int(limit)
    policy["daily_image_limit"] = profile["daily_image_limit"] if profile["allow_images"] and policy["cloud_allowed"] else 0
    allowed = {"read_document","artifacts"}
    if profile["allow_web"]:
        allowed.add("web_search")
    if profile["allow_images"] and policy["cloud_allowed"]:
        allowed.add("generate_image")
    policy["allowed_tools"] = sorted(set(policy.get("allowed_tools",[])) & allowed)
    if policy["cloud_allowed"]:
        policy["provider"] = "openai"
        policy["model"] = get_settings().openai_default_model
    if not policy["cloud_allowed"]:
        policy["allowed_providers"] = [p for p in policy["allowed_providers"] if p != "openai"]
    return policy
