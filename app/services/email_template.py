"""HTML email template rendering for Secretary Schedule."""

from html import escape


def _base_email_shell(*, header_title: str, content_html: str, subtitle: str = "Secretary Schedule") -> str:
    """Wrap content in a responsive, modern HTML email card."""
    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(header_title)}</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #0f172a; line-height: 1.6;">
  <table width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: #f8fafc; padding: 32px 12px;">
    <tr>
      <td align="center">
        <table width="100%" border="0" cellpadding="0" cellspacing="0" style="max-width: 580px; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05); border: 1px solid #e2e8f0;">
          <!-- Header -->
          <tr>
            <td style="background-color: #1e3a8a; background: linear-gradient(135deg, #1e40af, #2563eb); padding: 24px 28px; color: #ffffff;">
              <div style="font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; opacity: 0.9; margin-bottom: 4px;">{escape(subtitle)}</div>
              <h1 style="margin: 0; font-size: 20px; font-weight: 700; color: #ffffff; line-height: 1.3;">{escape(header_title)}</h1>
            </td>
          </tr>
          <!-- Body Content -->
          <tr>
            <td style="padding: 28px;">
              {content_html}
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background-color: #f1f5f9; padding: 18px 28px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #64748b; text-align: center; line-height: 1.5;">
              Email được gửi tự động từ <strong>Secretary Schedule</strong> theo cài đặt nhắc việc của bạn.<br>
              Thời gian hiển thị theo múi giờ Việt Nam (Asia/Ho_Chi_Minh, UTC+7).
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def render_near_reminder_html(
    *,
    title: str,
    starts_at: str,
    ends_at: str | None,
    meeting_mode: str,
    related_person: str,
    location: str | None = None,
    meeting_url: str | None = None,
    preparation: str | None = None,
    notes: str | None = None,
) -> str:
    """Render HTML for an upcoming schedule reminder."""
    mode_label = "Trực tuyến (Online)" if meeting_mode == "online" else "Trực tiếp (Offline)"
    end_str = ends_at if ends_at else "Không có giờ kết thúc"

    url_button = ""
    if meeting_url:
        safe_url = escape(meeting_url)
        url_button = f"""
        <div style="margin: 20px 0 12px 0;">
          <a href="{safe_url}" target="_blank" rel="noopener noreferrer" style="display: inline-block; background-color: #2563eb; color: #ffffff; text-decoration: none; font-size: 14px; font-weight: 600; padding: 10px 20px; border-radius: 6px; box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);">
            Tham gia cuộc họp
          </a>
          <div style="font-size: 12px; color: #64748b; margin-top: 6px; word-break: break-all;">
            Liên kết: <a href="{safe_url}" style="color: #2563eb;">{safe_url}</a>
          </div>
        </div>"""

    location_row = ""
    if location:
        location_row = f"""
        <tr>
          <td style="padding: 8px 0; color: #64748b; font-size: 14px; width: 140px; vertical-align: top;">Địa điểm:</td>
          <td style="padding: 8px 0; color: #0f172a; font-size: 14px; font-weight: 500;">{escape(location)}</td>
        </tr>"""

    prep_box = ""
    if preparation:
        prep_box = f"""
        <div style="margin-top: 16px; background-color: #eff6ff; border-left: 4px solid #3b82f6; padding: 12px 16px; border-radius: 4px;">
          <div style="font-size: 13px; font-weight: 600; color: #1e40af; margin-bottom: 2px;">Cần chuẩn bị:</div>
          <div style="font-size: 14px; color: #1e3a8a; white-space: pre-wrap;">{escape(preparation)}</div>
        </div>"""

    notes_box = ""
    if notes:
        notes_box = f"""
        <div style="margin-top: 12px; background-color: #f8fafc; border: 1px solid #e2e8f0; padding: 12px 16px; border-radius: 6px;">
          <div style="font-size: 13px; font-weight: 600; color: #475569; margin-bottom: 2px;">Ghi chú:</div>
          <div style="font-size: 14px; color: #334155; white-space: pre-wrap;">{escape(notes)}</div>
        </div>"""

    content = f"""
      <div style="display: inline-block; background-color: #fef3c7; color: #92400e; font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 9999px; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px;">
        Sắp diễn ra
      </div>
      <h2 style="margin: 0 0 16px 0; font-size: 20px; font-weight: 700; color: #0f172a;">{escape(title)}</h2>

      <table width="100%" border="0" cellpadding="0" cellspacing="0" style="border-collapse: collapse; margin-bottom: 12px;">
        <tr>
          <td style="padding: 8px 0; color: #64748b; font-size: 14px; width: 140px;">Bắt đầu:</td>
          <td style="padding: 8px 0; color: #0f172a; font-size: 14px; font-weight: 600;">{escape(starts_at)}</td>
        </tr>
        <tr>
          <td style="padding: 8px 0; color: #64748b; font-size: 14px;">Kết thúc:</td>
          <td style="padding: 8px 0; color: #0f172a; font-size: 14px;">{escape(end_str)}</td>
        </tr>
        <tr>
          <td style="padding: 8px 0; color: #64748b; font-size: 14px;">Hình thức:</td>
          <td style="padding: 8px 0; color: #0f172a; font-size: 14px;">{escape(mode_label)}</td>
        </tr>
        {location_row}
        <tr>
          <td style="padding: 8px 0; color: #64748b; font-size: 14px;">Người liên quan:</td>
          <td style="padding: 8px 0; color: #0f172a; font-size: 14px; font-weight: 500;">{escape(related_person)}</td>
        </tr>
      </table>

      {url_button}
      {prep_box}
      {notes_box}
    """
    return _base_email_shell(header_title=f"Nhắc lịch: {title}", content_html=content)


def render_daily_summary_html(
    *,
    date_str: str,
    schedules: list[dict[str, str | None]],
) -> str:
    """Render HTML for a daily schedule summary."""
    if not schedules:
        content = f"""
          <p style="font-size: 15px; color: #334155; margin: 0 0 12px 0;">
            Hôm nay (<strong>{escape(date_str)}</strong>), bạn không có lịch trình nào được xếp trên hệ thống.
          </p>
          <div style="background-color: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 8px; padding: 24px; text-align: center; color: #64748b; font-size: 14px;">
            Chúc bạn một ngày làm việc hiệu quả và thuận lợi!
          </div>
        """
    else:
        items_html = []
        for idx, item in enumerate(schedules, start=1):
            time_range = escape(item.get("time_range") or "")
            title = escape(item.get("title") or "")
            mode = escape(item.get("mode") or "")
            person = escape(item.get("related_person") or "")
            loc = item.get("location")
            loc_str = f" • Địa điểm: {escape(loc)}" if loc else ""
            url = item.get("meeting_url")
            link_str = f' • <a href="{escape(url)}" style="color: #2563eb; text-decoration: underline;">Link họp</a>' if url else ""

            item_card = f"""
            <div style="margin-bottom: 12px; padding: 14px 16px; background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;">
              <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px;">
                <span style="font-size: 13px; font-weight: 700; color: #2563eb; background-color: #eff6ff; padding: 2px 8px; border-radius: 4px;">{time_range}</span>
                <span style="font-size: 12px; color: #64748b;">#{idx}</span>
              </div>
              <div style="font-size: 15px; font-weight: 600; color: #0f172a; margin-top: 6px;">{title}</div>
              <div style="font-size: 13px; color: #64748b; margin-top: 4px;">
                Hình thức: {mode} • Người liên quan: {person}{loc_str}{link_str}
              </div>
            </div>"""
            items_html.append(item_card)

        count = len(schedules)
        content = f"""
          <p style="font-size: 15px; color: #334155; margin: 0 0 16px 0;">
            Hôm nay (<strong>{escape(date_str)}</strong>), bạn có <strong>{count}</strong> lịch trình:
          </p>
          {''.join(items_html)}
        """

    return _base_email_shell(header_title=f"Lịch trình ngày {date_str}", content_html=content)


def render_update_notification_html(
    *,
    title: str,
    version: int,
    starts_at: str,
    ends_at: str | None,
    meeting_mode: str,
    related_person: str,
    is_cancelled: bool,
) -> str:
    """Render HTML for schedule update or cancellation notification."""
    mode_label = "Trực tuyến (Online)" if meeting_mode == "online" else "Trực tiếp (Offline)"
    end_str = ends_at if ends_at else "Không có giờ kết thúc"

    if is_cancelled:
        badge = '<span style="display: inline-block; background-color: #fee2e2; color: #991b1b; font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 9999px; text-transform: uppercase;">Đã hủy</span>'
        header_title = f"Lịch trình đã hủy: {title}"
        msg = f"Lịch trình <strong>{escape(title)}</strong> vừa được hủy trên hệ thống."
    else:
        badge = f'<span style="display: inline-block; background-color: #dbeafe; color: #1e40af; font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 9999px; text-transform: uppercase;">Cập nhật (v{version})</span>'
        header_title = f"Cập nhật lịch: {title}"
        msg = f"Lịch trình <strong>{escape(title)}</strong> vừa được cập nhật sang phiên bản mới (v{version})."

    content = f"""
      <div style="margin-bottom: 12px;">{badge}</div>
      <p style="font-size: 15px; color: #334155; margin: 0 0 16px 0;">{msg}</p>

      <table width="100%" border="0" cellpadding="0" cellspacing="0" style="border-collapse: collapse; margin-bottom: 8px;">
        <tr>
          <td style="padding: 8px 0; color: #64748b; font-size: 14px; width: 140px;">Bắt đầu:</td>
          <td style="padding: 8px 0; color: #0f172a; font-size: 14px; font-weight: 600;">{escape(starts_at)}</td>
        </tr>
        <tr>
          <td style="padding: 8px 0; color: #64748b; font-size: 14px;">Kết thúc:</td>
          <td style="padding: 8px 0; color: #0f172a; font-size: 14px;">{escape(end_str)}</td>
        </tr>
        <tr>
          <td style="padding: 8px 0; color: #64748b; font-size: 14px;">Hình thức:</td>
          <td style="padding: 8px 0; color: #0f172a; font-size: 14px;">{escape(mode_label)}</td>
        </tr>
        <tr>
          <td style="padding: 8px 0; color: #64748b; font-size: 14px;">Người liên quan:</td>
          <td style="padding: 8px 0; color: #0f172a; font-size: 14px;">{escape(related_person)}</td>
        </tr>
      </table>
    """
    return _base_email_shell(header_title=header_title, content_html=content)


def render_password_reset_html(*, reset_url: str) -> str:
    """Render HTML for password reset email."""
    safe_url = escape(reset_url)
    content = f"""
      <p style="font-size: 15px; color: #334155; margin: 0 0 16px 0;">
        Chúng tôi nhận được yêu cầu đặt lại mật khẩu cho tài khoản Secretary Schedule của bạn.
      </p>
      <div style="text-align: center; margin: 28px 0;">
        <a href="{safe_url}" target="_blank" rel="noopener noreferrer" style="display: inline-block; background-color: #2563eb; color: #ffffff; text-decoration: none; font-size: 15px; font-weight: 600; padding: 12px 28px; border-radius: 6px; box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1);">
          Đặt lại mật khẩu
        </a>
      </div>
      <p style="font-size: 13px; color: #64748b; margin: 16px 0 0 0;">
        Liên kết này có hiệu lực trong vòng <strong>30 phút</strong>. Nếu nút bấm trên không hoạt động, hãy sao chép và dán liên kết sau vào trình duyệt:<br>
        <a href="{safe_url}" style="color: #2563eb; word-break: break-all;">{safe_url}</a>
      </p>
      <p style="font-size: 13px; color: #94a3b8; margin: 16px 0 0 0;">
        Nếu bạn không yêu cầu đặt lại mật khẩu, bạn có thể yên tâm bỏ qua email này.
      </p>
    """
    return _base_email_shell(header_title="Đặt lại mật khẩu", content_html=content)
