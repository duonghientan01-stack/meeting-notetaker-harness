# Tổng Hợp Nâng Cấp Hệ Thống: Meeting Intelligence Harness v3.2 (C-Team Enterprise)

**Dự án:** `meeting-notetaker-harness`  
**Ngày cập nhật:** 18/09/2026  
**Phiên bản:** `v3.2.0` (Production Live)  
**Môi trường Live (Render):** `https://meeting-notetaker-harness.onrender.com`  
**Bảng Monday mục tiêu:** C-Team Board (`5102468049`) | Group Staging: `NEW — Cross-Department Intake` (`group_mm6w83gk`)  
**Mã nguồn GitHub:** [duonghientan01-stack/meeting-notetaker-harness](https://github.com/duonghientan01-stack/meeting-notetaker-harness)  
**Commits chính:** `19139da`, `460c21a`  

---

## 1. Bối Cảnh & Các Yêu Cầu Phản Biện Nghiệp Vụ Cốt Lõi

Sau quá trình audit và phản biện thực tế với ban điều hành C-Team, hệ thống đã được tái cấu trúc triệt để theo các nguyên tắc vận hành sau:

1. **Chuẩn hóa Fingerprint `[MoM]` & Giữ nguyên Google Apps Script (GAS):**
   - GAS quét Gmail tự động và bắn email MoM sang Render: Đây là giải pháp tối ưu nhất ($0 chi phí, không cần đổi MX record).
   - Khóa chặt Fingerprint: Chỉ nhận các email có tiêu đề chứa tiền tố `[MoM]` (chấp nhận cả `Re: [MoM]`, `Fwd: [MoM]`, không phân biệt hoa thường).
2. **Mở Rộng Phạm Vi Cho Toàn Bộ C-Team:**
   - Loại bỏ định danh hẹp *"Striking Digital EU market"*. Hệ thống phục vụ toàn diện mọi mảng vận hành của C-Team: TVC & Video Production, Product R&D, Bao bì (Packaging), Chuỗi cung ứng (Supply Chain), Phân phối bán lẻ (Retail), Marketing, Pháp lý & Tài chính.
3. **Gán Việc Nhóm Cho TẤT CẢ Người Dự Họp (Multi-Person Group Assignment):**
   - Khi task là cam kết chung (*"All team members", "Team", "Everyone"*): Hệ thống **không gán riêng cho Dương Tấn**, cũng không giới hạn ở một vài cá nhân.
   - Hệ thống tự động quét danh sách tham dự cuộc họp (`meeting.participants`), tra cứu ID Monday từ toàn bộ danh bạ 13+ thành viên, và gán **toàn bộ người có mặt** vào cột `Owner` dạng Multiple Person trên Monday.
4. **Loại Bỏ Hoàn Toàn Tiền Tố Tên Người Trên Tiêu Đề:**
   - Tiêu đề task tuyệt đối không chứa `[Assignee Name]` để tránh trùng lặp thông tin với cột `Owner`.
   - Tiêu đề chuẩn: `⚡ <Imperative Action Verb in English>`.
5. **Chốt Chặn Chống Trùng Lặp Task Tiếp Diễn (Task Continuity Gate):**
   - Khi cuộc họp thảo luận về tiến độ công việc đang làm (*"Tan đang tiếp tục vẽ storyboard hôm trước"*), AI sẽ nhận diện là `ONGOING_STATUS_UPDATE` và **chặn không tạo task mới**, chỉ ghi nhận vào Executive Summary.
   - Chỉ các cam kết công việc mới (`NEW_COMMITMENT`) mới được sinh task trên Monday.
6. **Luật Cứng 100% Tiếng Anh (English Mandate):**
   - Mọi nội dung bắn lên Monday (Tiêu đề, Technical Guidelines, Context Update, Workstream) bắt buộc phải là tiếng Anh chuyên nghiệp, bất kể cuộc họp nói tiếng Việt hay tiếng Trung/Quảng Đông.
7. **Phân Loại Workstream Động (Open Domains):**
   - Xóa bỏ việc ép vào 5 danh mục cố định cũ. GPT-4o tự động xác định domain thực tế theo ngữ cảnh cuộc họp.
8. **Staging Tại Intake Group (`INTAKE_GROUP_ID`):**
   - Trong giai đoạn thử nghiệm, 100% task được gom về nhóm Staging (`group_mm6w83gk`) để người kiểm duyệt xem xét trước khi phân bổ về nhóm cá nhân.

---

## 2. Toàn Bộ Thay Đổi Kỹ Thuật Chi Tiết (Code & Architecture)

### A. Bóc Tách Bullet Points & Hỗ Trợ JSON Trong `email_parser.py`
- **Bộ lọc làm sạch ký tự gạch đầu dòng:**
  Hàm `parse_transcript_lines` được trang bị regex tiền xử lý:
  ```python
  clean_cand = re.sub(r'^[ \t]*[-*•–—]+[ \t]*', '', line_s)
  clean_cand = re.sub(r'^\(?\d+[\.\)][ \t]*', '', clean_cand).strip()
  ```
  Giúp các dòng MoM dạng `- Tan: Làm việc A` hoặc `• Alexa: Duyệt việc B` được bóc tách chính xác Speaker và Action Item, không bị gộp sai ngữ cảnh.
- **Cơ chế nạp kép (Dual-Ingestion):**
  Hàm `parse_email_to_envelope` tự động phát hiện và giải mã cả **raw RFC 822 MIME bytes** và **structured JSON payload** từ Google Apps Script.

### B. System Prompt v3.2 & Chốt Chặn Continuity Trong `llm_extractor.py`
- Bổ sung trường `continuity_type: str = "NEW_COMMITMENT"` vào Pydantic model `LLMActionItem`.
- Chốt chặn loại trừ task trùng lặp trước khi gửi sang Quality Gate:
  ```python
  continuity = getattr(item, "continuity_type", "NEW_COMMITMENT")
  if continuity in ("ONGOING_STATUS_UPDATE", "COMPLETED_RECAP"):
      continue
  ```
- **Multi-Person Dynamic Attendee Resolver:**
  Khi `spoken == "ALL_MEETING_ATTENDEES"` hoặc `commitment_type == "group"`, hệ thống tự động duyệt qua `meeting.participants`, tra cứu và gom toàn bộ User ID vào `resolved_user_ids`.
- **Làm sạch tiêu đề triệt để:**
  Tự động strip sạch các tiền tố `[Name]` hoặc `Name:` phát sinh từ mô hình ngôn ngữ.

### C. Mở Rộng Schema & Đồng Bộ Multi-Person Trong `monday_syncer.py`
- **Vá lỗi Fatal Bug:** Import thư viện `uuid` và `re`, loại bỏ hoàn toàn nguy cơ văng lỗi `NameError` khi đẩy dữ liệu vào Dead-Letter Queue (DLQ).
- **Hỗ trợ gán mảng nhiều người:**
  ```python
  resolved_user_ids = task.get("resolved_user_ids") or []
  if resolved_user_ids:
      col_vals[COLUMNS["assign_to"]] = {
          "personsAndTeams": [{"id": int(uid), "kind": "person"} for uid in resolved_user_ids]
      }
  ```
- **Đảm bảo cột Monitor luôn được điền:**
  - Creative / TVC / Marketing / Social ➔ Alexa Chan & Leah.
  - Systems / Automation / Workflow ➔ Mike Wong.

### D. Di Trú Cơ Sở Dữ Liệu SQLite Bền Vững Trong `db.py`
- Tự động chạy migration khi khởi động container:
  ```python
  ensure_column("tasks", "resolved_user_ids", "TEXT")
  ```
- Hàm `save_task()` lưu mảng ID dưới dạng JSON; hàm `get_tasks_for_meeting()` tự động deserialize thành `List[int]`, đảm bảo danh sách phân quyền nhiều người không bao giờ bị mất khi khởi động lại.

### E. Tối Ưu Hóa Container Trong `Dockerfile`
- Cập nhật `WORKDIR /app/meeting-notetaker-harness` và chạy lệnh `CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]` để loại bỏ lỗi module Python có dấu gạch ngang.

---

## 3. Bảng Đối Soát Cấu Trúc Bắn Task Lên Monday.com

| Cột trên Monday | Column ID | Quy Chuẩn Dữ Liệu Bắn Lên (v3.2) |
|---|---|---|
| **Item Name** | `name` | `⚡ <Imperative Task Name>` *(100% tiếng Anh, KHÔNG có [Tên Người])* |
| **Owner** | `multiple_person_mm523asb` | Single ID cá nhân (nếu là task riêng) HOẶC **Mảng toàn bộ User ID người dự họp** (nếu là task chung) |
| **👀 Monitor** | `multiple_person_mm6w75qm` | **Alexa & Leah** (Marketing/TVC) hoặc **Mike Wong** (Workflow/Ops) |
| **Status** | `color_mm5ken0m` | Luôn đặt `Not Started` |
| **Project Health** | `color_mm6cg534` | Luôn đặt `On Track` |
| **Due Date** | `date_mm6v7v2x` | YYYY-MM-DD tính từ cuộc họp (để trống nếu không nhắc tới, không bao giờ đoán mò) |
| **Priority** | `color_mm6cawvp` | `High` / `Medium` / `Low` |
| **Workstream** | `text_mm6c9xj9` | Tự động phân loại linh hoạt (*TVC & Video Production, Packaging, Supply Chain, Retail...*) |
| **Update Thread** | `create_update` | Thẻ HTML 100% tiếng Anh kèm Context Quote, Technical Guidelines, Monitors và link Recording |

---

## 4. Kết Quả Kiểm Thử (Verification Matrix)

Đã chạy toàn bộ test suite hồi quy và test suite tính năng mới:
```bash
python -m unittest discover tests/ -v
```
**Kết quả:**
```text
Ran 41 tests in 1.207s
OK
```
- **V-1 (Bullet Parsing):** Các định dạng `-`, `*`, `•`, `1.` bóc tách 100% chính xác speaker.
- **V-2 (Clean Title):** `[Tan] Prepare storyboard` ➔ chuẩn hóa thành `⚡ Prepare storyboard`.
- **V-3 (Team Assignment):** Task "ALL_MEETING_ATTENDEES" gán đầy đủ toàn bộ người dự họp vào cột `multiple_person`.
- **V-4 (Continuity Gate):** Báo cáo tiến độ đang làm bị chặn thành công, 0 task trùng lặp được tạo.
- **V-5 (GAS JSON Ingestion):** Payload JSON từ Google Apps Script giải mã hoàn hảo thành MeetingEnvelope v1.
- **V-6 (DLQ Resilience):** Thử nghiệm lỗi đồng bộ ghi nhận vào DLQ mượt mà, không còn lỗi `uuid`.

---

## 5. Xác Nhận Trạng Thái Triển Khai Thực Tế (Render Production)

Container phiên bản **v3.2.0** đã được build và deploy hoàn tất trên Render:
```bash
curl https://meeting-notetaker-harness.onrender.com/health
```
```json
{
  "status": "HEALTHY",
  "version": "3.2.0",
  "mode": "Shadow / Assisted Mode",
  "database": "/app/meeting-notetaker-harness/data/meeting_harness.sqlite",
  "active_users_cached": 13,
  "dlq_pending_count": 0,
  "daily_spend_usd": 0.0,
  "daily_budget_cap_usd": 5.0,
  "monday_token_configured": true,
  "timestamp": "2026-09-17T18:40:13.517379+00:00"
}
```

Hệ thống hiện đã ở trạng thái tối ưu nhất, sạch sẽ và sẵn sàng đón nhận các email MoM thực tế!
