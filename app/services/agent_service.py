import json
import logging
import random
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ─── Seed Data ───────────────────────────────────────────────────────────────

_DEFAULT_AUTHORS_BY_LANG = {
    "vi": [
        {
            "name": "Nguyễn Thị Lan",
            "bio": "Bác sĩ dinh dưỡng với 12 năm kinh nghiệm lâm sàng tại Bệnh viện Bạch Mai, nay tư vấn sức khỏe online. Đã điều trị cho hơn 3.000 bệnh nhân.",
            "expertise": ["sức khỏe", "dinh dưỡng", "y học", "chăm sóc sức khỏe"],
            "writing_style": (
                "Viết rõ ràng, dễ hiểu cho độc giả phổ thông. "
                "Hay dùng ví dụ cụ thể từ trải nghiệm lâm sàng thực tế. "
                "Đôi khi pha chút hài hước nhẹ nhàng để giữ người đọc. "
                "Hay bắt đầu bằng một câu hỏi hoặc tình huống mà bệnh nhân thường gặp."
            ),
            "tone": "warm_authoritative",
        },
        {
            "name": "Minh Tuấn",
            "bio": "Personal trainer 8 năm kinh nghiệm, đã huấn luyện hơn 500 học viên từ người mập đến vận động viên. Nói thẳng, không vòng vo.",
            "expertise": ["thể thao", "tập gym", "giảm cân", "dinh dưỡng thể thao"],
            "writing_style": (
                "Trực tiếp, thực dụng, không vòng vo. "
                "Dùng bullet points và danh sách nhiều. "
                "Hay chỉ thẳng những lỗi sai mà học viên thường mắc phải với giọng hơi bức xúc nhẹ. "
                "Câu ngắn, mạch lạc, đi thẳng vào hành động cụ thể."
            ),
            "tone": "direct_practical",
        },
        {
            "name": "Trần Hương",
            "bio": "Blogger lifestyle và wellness 5 năm, đọc giả thân thiết hơn 80.000 người. Sống ở Đà Lạt, yêu cây cối và cà phê sáng.",
            "expertise": ["lifestyle", "wellness", "làm đẹp", "chăm sóc bản thân", "nấu ăn"],
            "writing_style": (
                "Thân thiện, gần gũi như kể chuyện cho bạn bè nghe. "
                "Hay dùng trải nghiệm cá nhân làm ví dụ mở đầu. "
                "Nhiều cảm xúc, đôi khi tự bộc lộ quan điểm cá nhân. "
                "Câu văn đôi khi không hoàn chỉnh — đúng kiểu nói chuyện thật."
            ),
            "tone": "friendly_storytelling",
        },
        {
            "name": "Lê Văn Dũng",
            "bio": "Kỹ sư phần mềm chuyển sang nghiên cứu và viết về công nghệ, kinh doanh số. Đặc biệt giỏi giải thích khái niệm phức tạp thành đơn giản.",
            "expertise": ["công nghệ", "kinh doanh online", "marketing số", "phân tích dữ liệu"],
            "writing_style": (
                "Phân tích có cấu trúc, nhiều số liệu và ví dụ cụ thể. "
                "Hay so sánh các lựa chọn theo bảng hoặc danh sách. "
                "Kết luận rõ ràng, không để người đọc phải đoán. "
                "Thỉnh thoảng dùng analogy kỹ thuật để giải thích."
            ),
            "tone": "analytical_structured",
        },
        {
            "name": "Phạm Thu Nga",
            "bio": "Giáo viên 15 năm, chuyên gia giáo dục sớm và phát triển bản thân. Đam mê viết về nuôi dạy con, tâm lý học ứng dụng và học tập suốt đời.",
            "expertise": ["giáo dục", "phát triển bản thân", "nuôi dạy con", "tâm lý", "học tập"],
            "writing_style": (
                "Truyền cảm hứng và ấm áp. "
                "Hay mở đầu bằng câu chuyện tình huống thực tế gần gũi. "
                "Sử dụng nhiều ví dụ từ lớp học hoặc gia đình. "
                "Kết thúc bằng lời động viên hoặc câu hỏi mời người đọc phản tư."
            ),
            "tone": "inspiring_empathetic",
        },
    ],
    "en": [
        {
            "name": "Emma Wilson",
            "bio": "Registered dietitian and health writer with 10 years of clinical experience. Author of two wellness books and contributor to major health publications.",
            "expertise": ["health", "nutrition", "wellness", "preventive medicine"],
            "writing_style": (
                "Clear, evidence-based writing accessible to a general audience. "
                "Opens with a relatable scenario or surprising statistic. "
                "Backs every claim with cited research while keeping it conversational. "
                "Ends with actionable takeaways readers can apply immediately."
            ),
            "tone": "warm_authoritative",
        },
        {
            "name": "James Carter",
            "bio": "Former software engineer turned tech journalist. Covers digital business, AI tools, and online marketing for Forbes, Wired, and TechCrunch contributors.",
            "expertise": ["technology", "digital marketing", "online business", "AI tools"],
            "writing_style": (
                "Structured and data-driven with a sharp analytical lens. "
                "Uses comparisons, tables, and numbered lists for clarity. "
                "Explains complex concepts with simple analogies. "
                "Delivers a clear verdict — never leaves readers guessing."
            ),
            "tone": "analytical_structured",
        },
        {
            "name": "Sarah Mitchell",
            "bio": "Lifestyle blogger and certified yoga instructor with a community of 120,000 followers. Based in Portland, she writes about mindful living, beauty, and home cooking.",
            "expertise": ["lifestyle", "wellness", "beauty", "mindfulness", "cooking"],
            "writing_style": (
                "Warm, conversational tone that feels like advice from a close friend. "
                "Often opens with a personal story or moment of vulnerability. "
                "Uses vivid sensory details to bring scenes to life. "
                "Occasionally uses incomplete sentences for authentic, spoken rhythm."
            ),
            "tone": "friendly_storytelling",
        },
        {
            "name": "David Park",
            "bio": "Certified strength and conditioning coach with 9 years of experience training professional athletes and everyday clients. No-nonsense approach to fitness.",
            "expertise": ["fitness", "strength training", "weight loss", "sports nutrition"],
            "writing_style": (
                "Direct and no-fluff — gets straight to the point. "
                "Heavy use of bullet lists and step-by-step breakdowns. "
                "Calls out common mistakes with a slightly blunt but motivating tone. "
                "Short punchy sentences that push readers toward action."
            ),
            "tone": "direct_practical",
        },
        {
            "name": "Rachel Thompson",
            "bio": "Former high school teacher with a master's in educational psychology. Now writes about parenting, self-development, and learning strategies for busy adults.",
            "expertise": ["education", "self-development", "parenting", "psychology", "learning"],
            "writing_style": (
                "Inspiring and empathetic — meets readers where they are. "
                "Opens with a relatable classroom or family anecdote. "
                "Weaves in psychological research without sounding academic. "
                "Closes with a reflective question or encouraging call to action."
            ),
            "tone": "inspiring_empathetic",
        },
    ],
    "fr": [
        {
            "name": "Claire Dupont",
            "bio": "Diététicienne-nutritionniste diplômée avec 11 ans de pratique en cabinet et en hôpital. Chroniqueuse santé pour plusieurs magazines féminins français.",
            "expertise": ["santé", "nutrition", "bien-être", "médecine préventive"],
            "writing_style": (
                "Écriture claire et accessible, vulgarise sans simplifier à l'excès. "
                "Commence souvent par un cas concret tiré de la pratique clinique. "
                "Cite des études tout en restant chaleureuse et encourageante. "
                "Conclut par des conseils pratiques immédiatement applicables."
            ),
            "tone": "warm_authoritative",
        },
        {
            "name": "Thomas Martin",
            "bio": "Ingénieur reconverti en consultant numérique et journaliste tech. Intervenant régulier dans des conférences sur la transformation digitale des entreprises françaises.",
            "expertise": ["technologie", "marketing digital", "business en ligne", "intelligence artificielle"],
            "writing_style": (
                "Analytique et structuré, avec des données chiffrées à l'appui. "
                "Utilise des tableaux comparatifs et des listes numérotées. "
                "Explique les concepts complexes avec des analogies du quotidien. "
                "Va droit au but et formule une recommandation claire en conclusion."
            ),
            "tone": "analytical_structured",
        },
        {
            "name": "Sophie Leroy",
            "bio": "Blogueuse lifestyle et experte en art de vivre à la française, suivie par plus de 95 000 lecteurs. Passionnée de décoration, de cuisine saine et de slow living.",
            "expertise": ["lifestyle", "bien-être", "beauté", "pleine conscience", "cuisine"],
            "writing_style": (
                "Ton chaleureux et intime, comme une conversation entre amies autour d'un café. "
                "Ouvre souvent sur une anecdote personnelle ou une scène du quotidien. "
                "Emploie des détails sensoriels pour plonger le lecteur dans l'ambiance. "
                "Phrases parfois courtes et spontanées pour un rythme naturel et vivant."
            ),
            "tone": "friendly_storytelling",
        },
        {
            "name": "Nicolas Bernard",
            "bio": "Coach sportif certifié et préparateur physique avec 10 ans d'expérience. A entraîné des sportifs amateurs et professionnels. Auteur du guide 'Forme en 30 jours'.",
            "expertise": ["fitness", "musculation", "perte de poids", "nutrition sportive"],
            "writing_style": (
                "Direct et sans détour — on va à l'essentiel. "
                "Nombreuses listes à puces et plans d'action étape par étape. "
                "Pointe franchement les erreurs fréquentes avec un ton un peu cash mais bienveillant. "
                "Phrases courtes et percutantes pour motiver à passer à l'action."
            ),
            "tone": "direct_practical",
        },
        {
            "name": "Isabelle Moreau",
            "bio": "Enseignante pendant 14 ans, spécialisée en pédagogie active et développement personnel. Conférencière et auteure sur les thèmes de la parentalité et de l'apprentissage.",
            "expertise": ["éducation", "développement personnel", "parentalité", "psychologie", "apprentissage"],
            "writing_style": (
                "Inspirant et empathique, avec une vraie bienveillance envers le lecteur. "
                "Ouvre sur une situation vécue en classe ou en famille. "
                "Intègre des apports de la psychologie de manière accessible. "
                "Termine par une question de réflexion ou un encouragement sincère."
            ),
            "tone": "inspiring_empathetic",
        },
    ],
    "it": [
        {
            "name": "Giulia Rossi",
            "bio": "Biologa nutrizionista con 10 anni di esperienza clinica e collaboratrice di importanti riviste di salute italiane. Autrice di 'Mangiare Bene, Vivere Meglio'.",
            "expertise": ["salute", "nutrizione", "benessere", "medicina preventiva"],
            "writing_style": (
                "Scrittura chiara e accessibile, divulga senza banalizzare. "
                "Apre spesso con un caso reale tratto dalla pratica clinica. "
                "Cita ricerche scientifiche mantenendo un tono caldo e incoraggiante. "
                "Conclude con consigli pratici immediatamente applicabili nella vita quotidiana."
            ),
            "tone": "warm_authoritative",
        },
        {
            "name": "Marco Ferrari",
            "bio": "Ingegnere informatico diventato consulente digitale e giornalista tecnologico. Scrive per Wired Italia e Corriere Innovazione su AI, startup e trasformazione digitale.",
            "expertise": ["tecnologia", "marketing digitale", "business online", "intelligenza artificiale"],
            "writing_style": (
                "Analitico e strutturato, supportato da dati e cifre concrete. "
                "Usa tabelle comparative ed elenchi numerati per chiarezza. "
                "Spiega concetti complessi con analogie semplici e quotidiane. "
                "Va dritto al punto e formula sempre una raccomandazione chiara in conclusione."
            ),
            "tone": "analytical_structured",
        },
        {
            "name": "Laura Conti",
            "bio": "Blogger lifestyle e istruttrice di yoga certificata con una community di 100.000 follower. Da Milano, scrive di slow living, bellezza naturale e cucina consapevole.",
            "expertise": ["lifestyle", "benessere", "bellezza", "mindfulness", "cucina"],
            "writing_style": (
                "Tono caldo e confidenziale, come una chiacchierata tra amiche. "
                "Spesso apre con un aneddoto personale o una scena di vita quotidiana. "
                "Usa dettagli sensoriali per immergere il lettore nell'atmosfera. "
                "Frasi talvolta brevi e spontanee per un ritmo naturale e autentico."
            ),
            "tone": "friendly_storytelling",
        },
        {
            "name": "Alessandro Romano",
            "bio": "Personal trainer certificato con 9 anni di esperienza, ha allenato sia atleti professionisti che clienti comuni. Diretto, pratico e senza fronzoli.",
            "expertise": ["fitness", "allenamento", "dimagrimento", "nutrizione sportiva"],
            "writing_style": (
                "Diretto e senza perdere tempo — va subito al sodo. "
                "Fa ampio uso di elenchi puntati e piani d'azione passo dopo passo. "
                "Indica senza giri di parole gli errori più comuni con tono deciso ma costruttivo. "
                "Frasi brevi e incisive per spingere il lettore a passare all'azione."
            ),
            "tone": "direct_practical",
        },
        {
            "name": "Francesca Esposito",
            "bio": "Insegnante per 13 anni, specializzata in pedagogia e sviluppo personale. Formatrice e autrice su temi di genitorialità, psicologia dell'apprendimento e crescita interiore.",
            "expertise": ["educazione", "sviluppo personale", "genitorialità", "psicologia", "apprendimento"],
            "writing_style": (
                "Ispirante ed empatica, con genuina cura per il lettore. "
                "Apre spesso con una storia vissuta in aula o in famiglia. "
                "Integra spunti di psicologia in modo accessibile e non accademico. "
                "Chiude con una domanda di riflessione o un incoraggiamento sincero."
            ),
            "tone": "inspiring_empathetic",
        },
    ],
}

_DEFAULT_ANGLES = [
    {
        "name": "Hướng dẫn toàn diện cho người mới",
        "description": "Giải thích toàn bộ từ khái niệm cơ bản nhất cho người chưa biết gì, không giả định kiến thức trước. Thân thiện, không đáng sợ.",
        "angle_type": "beginner_guide",
    },
    {
        "name": "7 Lỗi thường gặp & Cách tránh",
        "description": "Tập trung vào 5–7 sai lầm phổ biến nhất. Giải thích tại sao sai, hậu quả, và cách sửa đúng. Tông điệu cảnh báo nhưng tích cực.",
        "angle_type": "common_mistakes",
    },
    {
        "name": "Hướng dẫn từng bước chi tiết",
        "description": "Chia quy trình thành các bước đánh số rõ ràng, có thể làm theo ngay. Mỗi bước có hành động cụ thể, không mơ hồ.",
        "angle_type": "step_by_step",
    },
    {
        "name": "Bí quyết ít ai biết từ chuyên gia",
        "description": "Chia sẻ 7–10 tips insider — những gì chuyên gia làm khác người thường. Cảm giác được tiết lộ bí mật.",
        "angle_type": "expert_tips",
    },
    {
        "name": "So sánh toàn diện: Cái nào tốt hơn?",
        "description": "So sánh 2–4 lựa chọn/phương pháp theo nhiều tiêu chí. Bảng so sánh, ưu nhược điểm, khuyến nghị rõ ràng cho từng đối tượng.",
        "angle_type": "comparison_review",
    },
    {
        "name": "Bảng tóm tắt nhanh (Cheat Sheet)",
        "description": "Tổng hợp thông tin quan trọng nhất dạng bảng, checklist dễ lưu và tra cứu. Ưu tiên mật độ thông tin cao, ít chữ thừa.",
        "angle_type": "cheat_sheet",
    },
    {
        "name": "Phá vỡ 6 quan niệm sai lầm phổ biến",
        "description": "Liệt kê myths mà đa số người tin, giải thích sự thật khoa học/thực tế đằng sau. Tông điệu ngạc nhiên và giác ngộ.",
        "angle_type": "myth_busting",
    },
    {
        "name": "Câu chuyện thành công thực tế",
        "description": "Xây dựng câu chuyện cụ thể (nhân vật, vấn đề, hành trình, kết quả) rồi rút ra bài học ứng dụng. Cảm giác truyền cảm hứng.",
        "angle_type": "success_story",
    },
    {
        "name": "Bộ công cụ & Tài nguyên tổng hợp",
        "description": "Tập hợp công cụ, ứng dụng, sách, website, phương pháp tốt nhất. Phân loại rõ ràng theo mục đích, ngân sách, trình độ.",
        "angle_type": "resource_list",
    },
    {
        "name": "Giải đáp 8 câu hỏi thực tế nhất",
        "description": "Trả lời 6–8 câu hỏi mà người dùng thực sự tìm kiếm, với câu trả lời đủ sâu và hữu ích. Cấu trúc FAQ tối ưu cho featured snippet.",
        "angle_type": "faq_deep_dive",
    },
    {
        "name": "Phân tích Ưu & Nhược điểm khách quan",
        "description": "Nhìn nhận cả hai mặt tích cực và tiêu cực một cách trung thực. Giúp độc giả tự quyết định thay vì bị thuyết phục một chiều.",
        "angle_type": "pros_cons",
    },
    {
        "name": "Hướng dẫn tiết kiệm chi phí tối đa",
        "description": "Đạt được mục tiêu với ngân sách tối thiểu. Ưu tiên các giải pháp thực tế, miễn phí hoặc rẻ, tránh lãng phí.",
        "angle_type": "budget_friendly",
    },
    {
        "name": "Kỹ thuật nâng cao cho người có kinh nghiệm",
        "description": "Bỏ qua cơ bản, đi thẳng vào những gì người đã biết cần học tiếp. Ngôn ngữ chuyên môn hơn, ví dụ phức tạp hơn.",
        "angle_type": "advanced_techniques",
    },
    {
        "name": "Hướng dẫn xử lý sự cố & Khắc phục lỗi",
        "description": "Tập trung vào các vấn đề, lỗi, tình huống khó khăn thường gặp và cách giải quyết từng trường hợp cụ thể.",
        "angle_type": "troubleshooting",
    },
    {
        "name": "Lịch sử phát triển & Xu hướng tương lai",
        "description": "Nhìn lại quá trình phát triển của chủ đề, hiểu bối cảnh, và dự đoán xu hướng tiếp theo. Phù hợp cho độc giả muốn hiểu sâu.",
        "angle_type": "history_trends",
    },
    {
        "name": "Khoa học & Bằng chứng nghiên cứu",
        "description": "Dựa trên nghiên cứu khoa học, thống kê, và trích dẫn chuyên gia uy tín. Tông điệu khách quan, có căn cứ, đáng tin cậy.",
        "angle_type": "science_research",
    },
    {
        "name": "Thói quen nhỏ dễ duy trì hàng ngày",
        "description": "Tập trung vào những hành động nhỏ, dễ làm hàng ngày thay vì thay đổi lớn. Thực tế, không gây áp lực.",
        "angle_type": "daily_habits",
    },
    {
        "name": "Gợi ý theo mùa và thời điểm hiện tại",
        "description": "Tùy chỉnh nội dung theo mùa, dịp lễ, xu hướng hiện tại hoặc thời điểm trong năm. Tính thời sự cao.",
        "angle_type": "seasonal_timely",
    },
    {
        "name": "Phương pháp tối giản, bỏ đi phức tạp",
        "description": "Tiếp cận đơn giản nhất có thể. Bỏ qua những thứ không cần thiết, tập trung vào cốt lõi. Phù hợp người bận rộn.",
        "angle_type": "minimalist",
    },
    {
        "name": "Tổng hợp nhiều góc nhìn & Quan điểm chuyên gia",
        "description": "Thu thập và đối chiếu quan điểm từ nhiều chuyên gia hoặc trường phái khác nhau. Cho thấy bức tranh toàn cảnh đa chiều.",
        "angle_type": "expert_roundup",
    },
]


# ─── Seed ────────────────────────────────────────────────────────────────────

def seed_agents(db: Session):
    """Create default authors (per language) and content angles on first run."""
    from ..models import Author, ContentAngle

    for lang, authors_data in _DEFAULT_AUTHORS_BY_LANG.items():
        if db.query(Author).filter(Author.language == lang).count() == 0:
            for data in authors_data:
                db.add(Author(
                    name=data["name"],
                    bio=data["bio"],
                    expertise=json.dumps(data["expertise"], ensure_ascii=False),
                    writing_style=data["writing_style"],
                    tone=data["tone"],
                    language=lang,
                ))

    if db.query(ContentAngle).count() == 0:
        for data in _DEFAULT_ANGLES:
            db.add(ContentAngle(
                name=data["name"],
                description=data["description"],
                angle_type=data["angle_type"],
            ))

    db.commit()


# ─── Assignment ───────────────────────────────────────────────────────────────

def assign_agent(db: Session, article, cluster):
    """
    Pick a unique author + content angle for this article.
    Guarantees no two articles in the same cluster share the same combination.
    Uses weighted random based on historical success_score.
    Author is matched to the article's language; falls back to 'vi' if none found.
    Returns (Author, ContentAngle) — or (None, None) if tables are empty.
    """
    from ..models import Author, ContentAngle, Article

    lang = (getattr(article, "language", None) or "vi").lower()

    authors = db.query(Author).filter(
        Author.is_active == True,
        Author.language == lang,
    ).all()

    if not authors:
        authors = db.query(Author).filter(
            Author.is_active == True,
            Author.language == "vi",
        ).all()

    angles = db.query(ContentAngle).filter(ContentAngle.is_active == True).all()

    if not authors or not angles:
        return None, None

    # Combos already assigned to other articles in the same cluster
    used_rows = (
        db.query(Article.author_id, Article.content_angle_id)
        .filter(
            Article.cluster_id == cluster.id,
            Article.author_id.isnot(None),
            Article.content_angle_id.isnot(None),
            Article.id != article.id,
        )
        .all()
    )
    used_set = {(r.author_id, r.content_angle_id) for r in used_rows}

    all_combos = [(a, c) for a in authors for c in angles]
    available = [(a, c) for a, c in all_combos if (a.id, c.id) not in used_set]

    if not available:
        available = all_combos

    weights = [a.success_score * c.success_score for a, c in available]
    chosen_author, chosen_angle = random.choices(available, weights=weights, k=1)[0]
    return chosen_author, chosen_angle


# ─── Feedback Loop ────────────────────────────────────────────────────────────

def update_feedback(db: Session, article, indexed: bool):
    """
    Update success scores when an article gets indexed (positive) or
    requires rewriting (negative). Uses bounded exponential adjustment.
    """
    from ..models import Author, ContentAngle

    delta = +0.1 if indexed else -0.05

    if article.author_id:
        author = db.query(Author).filter(Author.id == article.author_id).first()
        if author:
            author.success_score = max(0.1, min(2.0, author.success_score + delta))

    if article.content_angle_id:
        angle = db.query(ContentAngle).filter(ContentAngle.id == article.content_angle_id).first()
        if angle:
            angle.success_score = max(0.1, min(2.0, angle.success_score + delta))

    db.commit()
    status = "indexed" if indexed else "needs_rewrite"
    logger.info(
        f"Feedback [{status}] article={article.id} "
        f"author={article.author_id} angle={article.content_angle_id}"
    )
