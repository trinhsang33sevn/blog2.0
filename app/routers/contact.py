import logging
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from ..templates import templates
from ..services.email_service import send_contact_email

logger = logging.getLogger("autoblogspot.contact")

ADMIN_EMAIL = "hoangvandonglx@gmail.com"

router = APIRouter()


@router.get("/contact", response_class=HTMLResponse)
def contact_page(request: Request):
    return templates.TemplateResponse(request, "contact.html", {})


@router.post("/contact", response_class=HTMLResponse)
def contact_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    subject: str = Form(""),
    message: str = Form(...),
):
    if len(message.strip()) < 10:
        return templates.TemplateResponse(request, "contact.html", {
            "error": "Nội dung tin nhắn quá ngắn.",
            "name": name, "email": email, "subject": subject, "message": message,
        }, status_code=400)

    sent = send_contact_email(
        name=name.strip(),
        sender_email=email.strip(),
        message=f"Chủ đề: {subject}\n\n{message.strip()}" if subject else message.strip(),
        admin_email=ADMIN_EMAIL,
    )
    if not sent:
        logger.warning("Contact form from %s <%s> — email not configured, logging message: %s", name, email, message[:200])

    return templates.TemplateResponse(request, "contact.html", {"success": True})
