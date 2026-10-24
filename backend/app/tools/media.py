"""OpenAI image generation for FamilOS."""
import base64
import uuid
from ..config import get_settings
from ..models import FileAsset
from .registry import ToolContext, ToolResult, register

settings = get_settings()

@register("generate_image", "Genera un'immagine con OpenAI a partire da una descrizione.",
    {"type":"object","properties":{"prompt":{"type":"string"},"size":{"type":"string","enum":["1024x1024","1024x1536","1536x1024"]}},"required":["prompt"]},
    usage_kind="image")
async def generate_image(args: dict, ctx: ToolContext) -> ToolResult:
    if not ctx.policy.get("cloud_allowed",True):
        return ToolResult("Le immagini cloud non sono abilitate per il tuo profilo.",is_error=True)
    if not settings.openai_api_key:
        return ToolResult("Configura OpenAI per generare immagini.", is_error=True)
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return ToolResult("Descrizione vuota", is_error=True)
    if ctx.policy.get("moderation"):
        prompt = "Illustrazione sicura e adatta ai bambini. " + prompt
    from openai import AsyncOpenAI
    try:
        response = await AsyncOpenAI(api_key=settings.openai_api_key).images.generate(
            model=settings.image_model, prompt=prompt, size=args.get("size") or "1024x1024", n=1)
        encoded = response.data[0].b64_json
        if not encoded:
            return ToolResult("OpenAI non ha restituito l'immagine.", is_error=True)
        data = base64.b64decode(encoded)
        identifier = uuid.uuid4().hex
        path = settings.generated_dir / f"{identifier}.png"
        path.write_bytes(data)
        asset = FileAsset(id=identifier, family_id=ctx.user.family_id, owner_id=ctx.user.id,
            project_id=ctx.project_id, conversation_id=ctx.conversation_id, filename="immagine.png",
            mime="image/png", size=len(data), path=str(path), kind="generated")
        from ..services import workspace
        await workspace.place_in_conversation(ctx.db, asset)
        ctx.db.add(asset)
        await ctx.db.commit()
        return ToolResult("Immagine generata con OpenAI.", artifacts=[{"type":"image","file_id":identifier,"url":f"/files/{identifier}","mime":"image/png"}],
            usage={"provider":"openai","model":settings.image_model,"detail":"immagine"})
    except Exception as error:
        return ToolResult(f"Generazione immagine non riuscita: {error}", is_error=True)
