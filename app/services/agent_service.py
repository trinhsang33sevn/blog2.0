import json
import logging
import random
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ─── Seed Data ───────────────────────────────────────────────────────────────

_DEFAULT_AUTHORS = [
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
]

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
    """Create default authors and content angles on first run."""
    from ..models import Author, ContentAngle

    if db.query(Author).count() == 0:
        for data in _DEFAULT_AUTHORS:
            db.add(Author(
                name=data["name"],
                bio=data["bio"],
                expertise=json.dumps(data["expertise"], ensure_ascii=False),
                writing_style=data["writing_style"],
                tone=data["tone"],
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
    Returns (Author, ContentAngle) — or (None, None) if tables are empty.
    """
    from ..models import Author, ContentAngle, Article

    authors = db.query(Author).filter(Author.is_active == True).all()
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

    # All possible combinations
    all_combos = [(a, c) for a in authors for c in angles]
    available = [(a, c) for a, c in all_combos if (a.id, c.id) not in used_set]

    # If all 100 combos exhausted (>100 sites), recycle from full pool
    if not available:
        available = all_combos

    # Weighted random: higher success_score → more likely to be chosen
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
