# Tổng Hợp Nâng Cấp Hệ Thống: Meeting Intelligence Harness v3.1
**Dự án:** `meeting-notetaker-harness`  
**Ngày cập nhật:** 18/09/2026  
**Phiên bản:** v3.1.0  
**Môi trường Live (Render):** `https://meeting-notetaker-harness.onrender.com`  
**Bảng Monday mục tiêu:** C-Team Board (`5102468049`) | Group Intake (`group_mm6w83gk`)  
**Mã nguồn GitHub:** [duonghientan01-stack/meeting-notetaker-harness](https://github.com/duonghientan01-stack/meeting-notetaker-harness)

---

## 1. Bối Cảnh & Nguyên Nhân Lỗi Trước Khi Nâng Cấp (MoM TVC 30s)

Khi thử nghiệm biên bản cuộc họp TVC 30s Strikids từ HappyScribe, hệ thống v3.0 gặp 5 vấn đề cốt lõi:
1. **Assignee bị rớt về `[Needs Review]` và gán toàn bộ cho Dương Tấn:**  
   Khi container Render khởi động lại, cache danh bạ SQLite bắt đầu ở trạng thái 0 thành viên (`active_users_cached == 0`). Cơ chế an toàn trong `user_resolver.py` từ chối đoán mò nên rớt về Fallback, dẫn đến việc `monday_syncer.py` gán toàn bộ task cho Dương Tấn với tiêu đề `⚡ [Needs Review]`.
2. **Mất cấu trúc phân mục của MoM:**  
   Bộ regex cũ chỉ nhận dạng thoại miệng `Speaker: Text`, bỏ qua hoặc gom cụm các mục quan trọng như *Visual Direction*, *Production Workflow*, *Timeline & Coordination*.
3. **Mất hạn chót (Due Date) do thiếu liên kết chéo ngữ cảnh:**  
   Hạn chót nằm ở phần *Timeline* (*"the following day"*, *"September 30"*), trong khi đầu việc nằm ở *Action Items*. Model cũ (`gpt-4o-mini`) xử lý rời rạc nên không liên kết được ngày với task.
4. **Hardcode sai Workstream:**  
   Tất cả task đều bị gán cứng vào `Automation, Delivery & Reliability` thay vì phân loại đúng cho dự án sáng tạo / TVC.
5. **Bộ lọc tiếng ồn bắt nhầm task video:**  
   Regex lọc tiếng ồn cuộc gọi `\b(turn on|turn off|share).*\b(camera|screen|video)\b` đã vô tình chặn task *"Create and share initial storyboard/video draft..."*.

---

## 2. Toàn Bộ Nâng Cấp Kiến Trúc & Logic (v3.1)

### A. Nâng Cấp Model & Kiểm Soát Chi Phí
- **Chuyển sang OpenAI `gpt-4o`:** Tập trung độc quyền vào GPT-4o flagship (loại bỏ hoàn toàn Gemini API theo yêu cầu).
- **Hạn mức chi phí an toàn (Cost Gates):**
  - Giới hạn tối đa **$0.50 USD / cuộc họp**.
  - Trần ngân sách tối đa **$5.00 USD / ngày** (chi phí thực tế với `gpt-4o` chỉ khoảng ~$0.018 USD ≈ 450 VNĐ / cuộc họp).

### B. Bộ Xử Lý Dual-Mode Ingestion (`email_parser.py`)
- **Mode A (Conversational):** Xử lý biên bản hội thoại có mốc thời gian `[00:12:00] Speaker: Text`.
- **Mode B (Thematic MoM):** Nhận diện tài liệu tóm tắt cuộc họp có tiêu đề (*Visual Direction*, *Workflow*, *Timeline*, *Action Items*), giữ trọn vẹn ngữ cảnh kỹ thuật.
- Tự động lọc sạch footer quảng cáo của HappyScribe, Zoom, MS Teams.

### C. Đồng Bộ Danh Bạ Live & Nhúng Vào Prompt (`app.py`, `user_resolver.py`, `llm_extractor.py`)
- **Tự động nạp cache lúc khởi động (Startup Hydration):** `lifespan(app)` tự động gọi Monday GraphQL để kéo danh bạ 13 thành viên vào SQLite ngay khi Render boot.
- **Dự phòng an toàn (`CORE_TEAM_DEFAULTS`):** Cung cấp sẵn thông tin 13 thành viên để đảm bảo hệ thống không bao giờ bị rớt về `[Needs Review]` kể cả khi database rỗng.
- **Khớp chuẩn 100% với tên hiển thị thực tế trên Monday.com:**

| Monday User ID | Tên hiển thị trên Monday (`name`) | Email chính thức |
|---|---|---|
| **`113703761`** | **Duong Tan** | `tan.dh@poppingcandy.com.hk` |
| **`113704803`** | **Leah** *(không phải Leah Kung)* | `yhkung@striking.com.hk` |
| **`103551084`** | **Mike Wong** | `mikewong@striking.com.hk` |
| **`103982652`** | **Alexa Chan** | `alexachan@striking.com.hk` |
| **`112035594`** | **Emmy Chan** | `emmychan@striking.com.hk` |
| **`113703758`** | **Thossapong Sasipiyanon** | `tt@strikids.com` |
| **`107995985`** | **Jerry Chong** | `jerrychong@striking.com.hk` |
| **`103982654`** | **Wanlee** *(không phải Wanlee Ng)* | `wanleeng@striking.com.hk` |
| **`103982655`** | **Wayne Chan** | `waynechan@striking.com.hk` |
| **`107996894`** | **Alex Chan** | `alexchan@striking.com.hk` |
| **`107996903`** | **Leo Lai** | `leolai@striking.com.hk` |
| **`108225291`** | **Hayson Yung** | `haysonyung@striking.com.hk` |
| **`108807098`** | **Alberta Lai** | `albertalai@striking.com.hk` |

### D. Quy Chuẩn Phân Quyền Monitor C-Team (`monday_syncer.py`, `config.py`)
Tự động gán người giám sát vào cột **`👀 Monitor` (`multiple_person_mm6w75qm`)**:
- **Marketing, TVC, Creative, Social Ads, Media, Chiến dịch:**  
  ➔ Gán đồng thời **Alexa Chan (`103982652`)** & **Leah (`113704803`)**.
- **Workflow công ty, Tự động hoá, Vận hành hệ thống, Giám sát tổng:**  
  ➔ Gán **Mike Wong (`103551084`)**.

### E. Chuẩn Hoá Tiêu Đề Task Sạch Sẽ (Loại Bỏ Tiền Tố Tên Người)
- Loại bỏ hoàn toàn tiền tố tên người thừa thãi (`[Duong Tan]`, `[Alexa Chan]`) vì cột `Owner` trên Monday đã hiển thị đầy đủ avatar và tên.
- **Tiêu đề chuẩn:** Bắt đầu bằng biểu tượng AI `⚡` và động từ hành động trực tiếp.  
  *Ví dụ:* `⚡ Create and share the initial storyboard/video draft, including proposed character actions and transitions.`

---

## 3. Kết Quả Kiểm Thử Thực Tế (MoM TVC 30s)

Toàn bộ **36/36 unit test & regression test** đều pass 100% (`Ran 36 tests in 1.264s, OK`).

### Bảng Kết Quả Dry-Run Tạo Task Trên Monday:

| # | Tiêu Đề Task (Sạch Sẽ) | Người Thực Hiện (`Owner`) | Người Giám Sát (`👀 Monitor`) | Workstream Phân Loại | Hạn Chót |
|---|---|---|---|---|---|
| **1** | `⚡ Create and share the initial storyboard/video draft...` | **Duong Tan** (`113703761`) | **Alexa Chan** & **Leah** | TVC & Creative Video Production | **2026-09-19** (Ngày mai) |
| **2** | `⚡ Identify scenes and visual elements that require Emmy’s custom drawings.` | **Duong Tan** (`113703761`) | **Alexa Chan** & **Leah** | TVC & Creative Video Production | *Chưa ấn định* |
| **3** | `⚡ Send the updated script, 30-second audio, full song...` | **Alexa Chan** (`103982652`) | **Alexa Chan** & **Leah** | TVC & Creative Video Production | *Chưa ấn định* |
| **4** | `⚡ Create or revise character illustrations based on Tan’s draft.` | **Emmy Chan** (`112035594`) | **Alexa Chan** & **Leah** | TVC & Creative Video Production | *Chưa ấn định* |
| **5** | `⚡ Photograph or film suitable popping-candy visuals and splash effects.` | **Alexa Chan** (`103982652`) | **Alexa Chan** & **Leah** | TVC & Creative Video Production | **2026-09-30** |
| **6** | `⚡ Restore Dinky’s missing frame and confirm the final sequence.` | **Duong Tan / Team** (`113703761`) | **Alexa Chan** & **Leah** | TVC & Creative Video Production | **2026-09-30** |
| **7** | `⚡ Create a dedicated WhatsApp group and add project members...` | **Alexa Chan** (`103982652`) | **Alexa Chan** & **Leah** | TVC & Creative Video Production | *Chưa ấn định* |
| **8** | `⚡ Review the first draft and coordinate revisions toward Sept 30...` | **Duong Tan / Team** (`113703761`) | **Alexa Chan** & **Leah** | TVC & Creative Video Production | **2026-09-30** |

**Thống kê:**
- **Số task trích xuất:** 8/8 task (100% đầy đủ).
- **Số task bị `[Needs Review]`:** **0 task**.
- **Cột Update Thread:** Kèm theo khung xanh `🛠️ Technical Guidelines & Context` (kẹo nổ cạnh sắc, splash effect chân thực, phục hồi frame Dinky) và mục `👀 Monitor(s): Alexa Chan (Marketing), Leah (Marketing)`.

---

## 4. Lịch Sử Commit & Trạng Thái Triển Khai (Deployment)

1. **Commit `b45bfbc`:**  
   *`feat: upgrade to v3.1 (gpt-4o, dual-mode MoM ingestion, team directory injection, dynamic workstreams, startup cache hydration)`*
2. **Commit `2580053`:**  
   *`feat: implement C-team monitor governance (Alexa & Leah for marketing, Mike for workflow/general) and remove person name from item title`*
3. **Commit `fac7197`:**  
   *`fix: align member display names with live Monday directory (Leah, Wanlee, Duong Tan) and update resolver mappings`*

Tất cả đã được push lên nhánh `master` trên GitHub:  
`https://github.com/duonghientan01-stack/meeting-notetaker-harness.git`  
Dịch vụ trên **Render** đã tự động kích hoạt tiến trình build lại và áp dụng toàn bộ tính năng mới.
