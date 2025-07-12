import streamlit as st
from supabase import create_client, Client
import pandas as pd
from datetime import datetime, timezone
from collections import defaultdict
from itertools import groupby
import requests
from zoneinfo import ZoneInfo
import re
import unicodedata
import time

# --- Page Configuration ---
st.set_page_config(
    layout="centered",
    page_title="Trang Nhân viên",
    page_icon="🧑‍💻"
)

# --- Supabase Connection ---
@st.cache_resource
def init_supabase_client() -> Client:
    """Initializes and returns a Supabase client for employee app."""
    try:
        url = st.secrets["supabase_new"]["url"]
        key = st.secrets["supabase_new"]["anon_key"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Lỗi cấu hình Supabase. Vui lòng kiểm tra file .streamlit/secrets.toml. Chi tiết: {e}")
        st.stop()

supabase = init_supabase_client()

# --- Functions ---
@st.cache_data(ttl=60)
def fetch_my_tasks(user_id: str):
    """Fetches tasks assigned to the current logged-in user, ordered by due date."""
    try:
        response = supabase.table('tasks').select('*, projects(project_name, id, old_project_ref_id)').eq('assigned_to', user_id).order('due_date', desc=False).execute()
        return response.data if response.data else []
    except Exception as e:
        st.error(f"Lỗi khi tải công việc: {e}")
        return []

@st.cache_data(ttl=30)
def fetch_comments(task_id: int):
    """Fetches comments for a specific task, joining with profile info."""
    try:
        response = supabase.table('comments').select('*, profiles(full_name, role), attachment_url').eq('task_id', task_id).order('created_at', desc=True).execute()
        return response.data if response.data else []
    except Exception as e:
        st.error(f"Lỗi khi tải bình luận: {e}")
        return []

@st.cache_data(ttl=60)
def fetch_read_statuses(_supabase_client: Client, user_id: str):
    """Fetches all read statuses for the user, returns a dict of task_id -> UTC datetime."""
    try:
        response = _supabase_client.table('task_read_status').select('task_id, last_read_at').eq('user_id', user_id).execute()
        if response.data:
            # Luôn chuyển đổi sang UTC để so sánh nhất quán
            return {item['task_id']: datetime.fromisoformat(item['last_read_at']).astimezone(timezone.utc) for item in response.data}
        return {}
    except Exception as e:
        st.error(f"Lỗi khi tải trạng thái đã đọc: {e}")
        return {}

def mark_task_as_read(_supabase_client: Client, task_id: int, user_id: str):
    """Upserts the last read time for a user and a task using current UTC time."""
    try:
        # THÊM on_conflict='task_id, user_id' để Supabase biết cách xử lý trùng lặp
        _supabase_client.table('task_read_status').upsert(
            {
                'task_id': task_id,
                'user_id': user_id,
                'last_read_at': datetime.now(timezone.utc).isoformat()
            },
            on_conflict='task_id, user_id'  # Dòng quan trọng được thêm vào
        ).execute()
    except Exception as e:
        # In ra lỗi chi tiết hơn để dễ chẩn đoán nếu vẫn xảy ra
        print(f"Không thể đánh dấu đã đọc cho task {task_id}: {e}")

def get_deadline_color(due_date_str: str) -> str:
    """
    Trả về mã màu nền dựa trên thời gian còn lại đến hạn chót.
    - Đỏ: < 3 ngày hoặc quá hạn
    - Cam: 3-7 ngày
    - Vàng: 7-15 ngày
    - Xanh: > 15 ngày
    """
    if not due_date_str:
        return "#f5f5f5"  # Màu xám nhạt nếu không có deadline

    try:
        # Đặt múi giờ Việt Nam
        local_tz = ZoneInfo("Asia/Ho_Chi_Minh")
        
        # Chuyển đổi deadline và thời gian hiện tại sang cùng múi giờ
        due_date = datetime.fromisoformat(due_date_str).astimezone(local_tz)
        now = datetime.now(local_tz)
        
        time_remaining = due_date - now
        days_remaining = time_remaining.days

        if days_remaining < 3:
            return "#ffebee"  # Đỏ nhạt
        elif 3 <= days_remaining < 7:
            return "#fff3e0"  # Cam nhạt
        elif 7 <= days_remaining < 15:
            return "#fffde7"  # Vàng nhạt
        else:
            return "#e8f5e9"  # Xanh nhạt
            
    except (ValueError, TypeError):
        return "#f5f5f5"  # Trả về màu xám nếu có lỗi
    
def sanitize_filename(filename: str) -> str:
    """
    "Làm sạch" tên file: chuyển thành chữ không dấu, bỏ ký tự đặc biệt,
    thay thế khoảng trắng bằng gạch nối.
    """
    # Chuyển chuỗi unicode (có dấu) thành dạng gần nhất không dấu
    value = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii')
    # Loại bỏ các ký tự không phải là chữ, số, dấu gạch dưới, gạch nối, dấu chấm
    value = re.sub(r'[^\w\s.-]', '', value).strip()
    # Thay thế một hoặc nhiều khoảng trắng/gạch nối bằng một gạch nối duy nhất
    value = re.sub(r'[-\s]+', '-', value)
    return value

# HÀM CHẨN ĐOÁN DÀNH RIÊNG CHO EMPLOYEE_APP.PY
def add_comment(task_id: int, user_id: str, content: str, uploaded_file=None):
    """Thêm bình luận mới, với file đính kèm đã được làm sạch tên."""
    attachment_url = None
    attachment_original_name = None 

    if uploaded_file:
        if uploaded_file.size > 2 * 1024 * 1024:
            st.error("Lỗi: Kích thước file không được vượt quá 2MB.")
            return

        # Lưu lại tên gốc để hiển thị
        attachment_original_name = uploaded_file.name
        
        # FIX: Làm sạch tên file trước khi tạo đường dẫn
        sanitized_name = sanitize_filename(uploaded_file.name)
        file_path = f"task_{task_id}/{user_id}_{int(datetime.now().timestamp())}_{sanitized_name}"
        
        try:
            # Dùng client 'supabase' cho employee
            supabase.storage.from_("task-attachments").upload(
                file=uploaded_file.getvalue(),
                path=file_path,
                file_options={"content-type": uploaded_file.type}
            )
            attachment_url = supabase.storage.from_("task-attachments").get_public_url(file_path)
        except Exception as e:
            st.error(f"Lỗi khi tải file lên: {e}")
            return

    try:
        insert_data = {
            'task_id': task_id,
            'user_id': user_id,
            'content': content,
            'attachment_url': attachment_url,
            'attachment_original_name': attachment_original_name
        }
        # Dùng client 'supabase' cho employee
        supabase.table('comments').insert(insert_data).execute()
        st.cache_data.clear()
        st.toast("Đã gửi bình luận!", icon="💬")
    except Exception as e:
        st.error(f"Lỗi khi thêm bình luận: {e}")

def update_task_status(task_id: int, new_status: str):
    """Updates the status of a specific task."""
    try:
        supabase.table('tasks').update({'status': new_status}).eq('id', task_id).execute()
        st.cache_data.clear()
        st.toast(f"Đã cập nhật trạng thái!", icon="🔄")
    except Exception as e:
        st.error(f"Lỗi khi cập nhật trạng thái: {e}")

def change_password(new_password: str):
    """Updates the password for the currently logged-in user."""
    try:
        supabase.auth.update_user({"password": new_password})
        st.success("✅ Đổi mật khẩu thành công! Vui lòng sử dụng mật khẩu mới ở lần đăng nhập sau.")
    except Exception as e:
        st.error(f"Lỗi khi đổi mật khẩu: {e}")

# --- Main App Logic ---
if 'user' not in st.session_state:
    st.session_state.user = None

# --- Login UI ---
if st.session_state.user is None:
    
    if 'logout_message' in st.session_state:
        st.warning(st.session_state.logout_message)
        del st.session_state.logout_message # Xóa thông báo để không hiển thị lại

    st.title("🧑‍💻 Đăng nhập hệ thống")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Mật khẩu", type="password")
        submitted = st.form_submit_button("Đăng nhập")
        if submitted:
            try:
                user_response = supabase.auth.sign_in_with_password({"email": email, "password": password})
                st.session_state.user = user_response.user
                st.rerun()
            except Exception as e:
                st.error("Email hoặc mật khẩu không đúng. Vui lòng thử lại.")

# --- Main App UI (after login) ---
else:
    # ===================================================================
    # BẮT ĐẦU: LOGIC KIỂM TRA KHÔNG HOẠT ĐỘNG
    # ===================================================================
    TIMEOUT_IN_SECONDS = 1800 # 30 phút

    is_expired = False
    if 'last_activity_time' in st.session_state:
        idle_duration = time.time() - st.session_state.last_activity_time
        if idle_duration > TIMEOUT_IN_SECONDS:
            is_expired = True

    if is_expired:
        # Nếu ĐÃ HẾT HẠN: Hiển thị cảnh báo và không làm gì thêm.
        # Việc không cập nhật last_activity_time sẽ giữ cho trạng thái is_expired=True ở các lần chạy lại sau.
        st.error(
            "**Phiên làm việc đã hết hạn!** "
            "Để bảo mật, mọi thao tác đã được vô hiệu hóa. "
            "Vui lòng sao chép lại nội dung bạn đang soạn (nếu có), sau đó **Đăng xuất** và đăng nhập lại."
        )
    else:
        # Nếu CHƯA HẾT HẠN: Cập nhật lại thời gian hoạt động.
        # Chỉ cập nhật trong trường hợp này.
        st.session_state.last_activity_time = time.time()
    # ===================================================================
    # KẾT THÚC: LOGIC KIỂM TRA KHÔNG HOẠT ĐỘNG
    # ===================================================================
    user = st.session_state.user
    
    profile_res = supabase.table('profiles').select('account_status, role').eq('id', user.id).single().execute()
    if profile_res.data and profile_res.data.get('account_status') == 'inactive':
        st.error("Tài khoản của bạn đã bị vô hiệu hóa. Vui lòng liên hệ quản lý.")
        if st.button("Đăng xuất"):
            supabase.auth.sign_out()
            st.session_state.user = None
            st.rerun()
        st.stop()
    
    user_role = profile_res.data.get('role', 'employee') if profile_res.data else 'employee'

    st.title(f"Chào mừng, {user.user_metadata.get('full_name', user.email)}!")
    # Sử dụng cột để đặt các nút cạnh nhau
    col1, col2, _ = st.columns([0.2, 0.2, 0.6]) 

    if col1.button("Đăng xuất"):
        supabase.auth.sign_out()
        st.session_state.user = None
        st.rerun()

    if col2.button("🔄 Làm mới"):
        # Xóa cache để buộc tải lại công việc mới
        st.cache_data.clear()
        st.toast("Đã làm mới dữ liệu!", icon="🔄")
        # Chạy lại ứng dụng để hiển thị dữ liệu mới
        st.rerun()
        
    with st.expander("🔑 Đổi mật khẩu"):
        with st.form("change_password_form_emp", clear_on_submit=True):
            new_password = st.text_input("Mật khẩu mới", type="password")
            confirm_password = st.text_input("Xác nhận mật khẩu mới", type="password")
            submitted_pw_change = st.form_submit_button("Lưu mật khẩu mới", disabled=is_expired)

            if submitted_pw_change and not is_expired:
                if not new_password or not confirm_password:
                    st.warning("Vui lòng nhập đầy đủ mật khẩu mới và xác nhận.")
                elif new_password != confirm_password:
                    st.error("Mật khẩu xác nhận không khớp!")
                elif len(new_password) < 6:
                    st.error("Mật khẩu phải có ít nhất 6 ký tự.")
                else:
                    change_password(new_password)
    
    st.divider()

    st.header("Công việc của bạn")
    st.markdown("""
    <style>
    .color-box {
        width: 15px;
        height: 15px;
        display: inline-block;
        border: 1px solid #ccc;
        vertical-align: middle;
        margin-right: 5px;
    }
    </style>
    <b>Chú thích Deadline:</b>
    <span class="color-box" style="background-color: #ffebee;"></span> < 3 ngày
    <span class="color-box" style="background-color: #fff3e0;"></span> 3-7 ngày
    <span class="color-box" style="background-color: #fffde7;"></span> 7-15 ngày
    <span class="color-box" style="background-color: #e8f5e9;"></span> > 15 ngày
    """, unsafe_allow_html=True)
    st.text("") # Thêm một khoảng trống nhỏ

    my_tasks = fetch_my_tasks(user.id)

    if not my_tasks:
        st.info("🎉 Bạn không có công việc nào cần làm. Hãy tận hưởng thời gian rảnh!")
    else:
        local_tz = ZoneInfo("Asia/Ho_Chi_Minh")
        # Tải trạng thái đã đọc (đã được chuyển sang UTC trong hàm)
        read_statuses = fetch_read_statuses(supabase, user.id)

        tasks_by_project = defaultdict(list)
        for task in my_tasks:
            project_info = task.get('projects')
            project_key = (project_info.get('project_name', 'Dự án không tên'), project_info.get('old_project_ref_id')) if project_info else ("Công việc chung", None)
            tasks_by_project[project_key].append(task)
        
        sorted_projects = sorted(tasks_by_project.items(), key=lambda item: min(t['due_date'] for t in item[1]))

        for (project_name, project_code), tasks in sorted_projects:
            display_title = f"Dự án: {project_name}" + (f" (Mã: {project_code})" if project_code else "")
            st.subheader(display_title)

            sorted_tasks_in_project = sorted(tasks, key=lambda t: (t.get('due_date') is None, t.get('due_date')))
            task_counter = 0

            for task in tasks:
                task_counter += 1
                comments = fetch_comments(task['id'])
                # ==========================================================
                # # DÁN ĐOẠN CODE CHẨN ĐOÁN TẠM THỜI 
                # st.markdown("---")
                # st.json({
                #     "TASK ID": task['id'],
                #     "TASK NAME": task['task_name']
                # })
                
                # last_read_time_utc = read_statuses.get(task['id'], datetime.fromtimestamp(0, tz=timezone.utc))

                # last_event_time_utc = datetime.fromisoformat(task['created_at']).astimezone(timezone.utc)
                # if comments:
                #     last_comment_time_utc = datetime.fromisoformat(comments[0]['created_at']).astimezone(timezone.utc)
                #     if last_comment_time_utc > last_event_time_utc:
                #         last_event_time_utc = last_comment_time_utc

                # st.code(f"Thời gian sự kiện cuối (UTC): {last_event_time_utc}")
                # st.code(f"Thời gian đọc cuối (UTC):   {last_read_time_utc}")

                # is_new = last_event_time_utc > last_read_time_utc
                # st.info(f"So sánh (Sự kiện > Đọc cuối): {is_new}")
                # # KẾT THÚC ĐOẠN CODE CHẨN ĐOÁN
                # # ==========================================================
                
                # --- LOGIC THÔNG BÁO MỚI (ĐÃ SỬA LỖI) ---
                status_icon = ""
                has_new_message = False

                last_read_time_utc = read_statuses.get(task['id'], datetime.fromtimestamp(0, tz=timezone.utc))

                last_event_time_utc = datetime.fromisoformat(task['created_at']).astimezone(timezone.utc)
                if comments:
                    last_comment_time_utc = datetime.fromisoformat(comments[0]['created_at']).astimezone(timezone.utc)
                    if last_comment_time_utc > last_event_time_utc:
                        last_event_time_utc = last_comment_time_utc

                if comments and comments[0]['user_id'] == user.id:
                    status_icon = "✅ Đã trả lời"
                elif last_event_time_utc > last_read_time_utc:
                    status_icon = "💬 Mới!"
                    has_new_message = True
                elif comments:
                    status_icon = "✔️ Đã xem"

                # --- Logic mới: Kiểm tra nhiệm vụ có bị quá hạn không ---
                is_overdue = False
                if task.get('due_date'):
                    try:
                        due_date = datetime.fromisoformat(task['due_date']).astimezone(local_tz)
                        if due_date < datetime.now(local_tz):
                            is_overdue = True
                    except (ValueError, TypeError):
                        is_overdue = False

                # --- Chuẩn bị các dòng thông tin để hiển thị ---
                # Dòng 1: Số thứ tự và Tên công việc
                line_1 = f"**Task {task_counter}. {task['task_name']}**"

                # Dòng 2: Trạng thái và Deadline
                try:
                    formatted_due_date = datetime.fromisoformat(task['due_date']).astimezone(local_tz).strftime('%d/%m/%Y, %H:%M')
                except (ValueError, TypeError):
                    formatted_due_date = 'N/A'

                line_2_parts = [
                    status_icon,
                    f"Trạng thái: *{task['status']}*",
                    f"Deadline: *{formatted_due_date}*"
                ]
                line_2 = " | ".join(filter(None, line_2_parts))

                # --- Hiển thị ra giao diện ---
                deadline_color = get_deadline_color(task.get('due_date'))
                st.markdown(f'<div style="background-color: {deadline_color}; border-radius: 7px; padding: 10px; margin-bottom: 10px;">', unsafe_allow_html=True)

                st.markdown(f"<span style='color: blue;'>{line_1}</span>", unsafe_allow_html=True)
                st.markdown(line_2)

                # Hiển thị cảnh báo nếu quá hạn VÀ chưa được hoàn thành
                if is_overdue and task.get('status') != 'Done':
                    st.markdown("<span style='color: red;'><b>Lưu ý: Nhiệm vụ đã quá hạn hoặc bạn chưa chuyển trạng thái Done khi đã làm xong</b></span>", unsafe_allow_html=True)

                with st.expander("Chi tiết & Thảo luận"):
                    # LOGIC MỚI: Chỉ đánh dấu đã đọc khi người dùng bấm nút
                    if has_new_message:
                        if st.button("✔️ Đánh dấu đã đọc", key=f"read_emp_{task['id']}", help="Bấm vào đây để xác nhận bạn đã xem tin nhắn mới nhất.", disabled=is_expired) and not is_expired:
                            mark_task_as_read(supabase, task['id'], user.id)
                            fetch_read_statuses.clear()
                            st.rerun()
                        st.divider()

                    # --- Toàn bộ code hiển thị chi tiết, thảo luận... của bạn vẫn giữ nguyên ở đây ---
                    st.markdown("#### Chi tiết công việc")
                    col1, col2 = st.columns(2)
                    with col1:
                        if task['description']:
                            st.markdown(task['description'])
                    
                    with col2:
                        status_options = ['To Do', 'In Progress', 'Done']
                        current_status_index = status_options.index(task['status']) if task['status'] in status_options else 0
                        new_status = st.selectbox(
                            "Cập nhật trạng thái:",
                            options=status_options,
                            index=current_status_index,
                            key=f"status_{task['id']}",
                            disabled=is_expired
                        )
                        if new_status != task['status'] and not is_expired:
                            update_task_status(task['id'], new_status)
                            st.rerun()

                    st.divider()

                    st.markdown("#### Thảo luận")
                    with st.container(height=250):
                        if not comments:
                            st.info("Chưa có bình luận nào cho công việc này.", icon="📄")
                        else:
                            for comment in comments:
                                commenter_name = comment.get('profiles', {}).get('full_name', "Người dùng ẩn")
                                is_manager = comment.get('profiles', {}).get('role') == 'manager'
                                comment_time_local = datetime.fromisoformat(comment['created_at']).astimezone(local_tz).strftime('%H:%M, %d/%m/%Y')
                                
                                st.markdown(
                                    f"<div style='border-left: 3px solid {'#ff4b4b' if is_manager else '#007bff'}; padding-left: 10px; margin-bottom: 10px;'>"
                                    f"<b>{commenter_name}</b> {'(Quản lý)' if is_manager else ''} <span style='font-size: 0.8em; color: gray;'><i>({comment_time_local})</i></span>:<br>"
                                    f"{comment['content']}"
                                    "</div>",
                                    unsafe_allow_html=True
                                )

                                if comment.get('attachment_url'):
                                    url = comment['attachment_url']
                                    file_name = comment.get('attachment_original_name', 'downloaded_file')
                                    
                                    try:
                                        response = requests.get(url)
                                        response.raise_for_status()
                                        st.download_button(
                                            label="📂 Tải file đính kèm",
                                            data=response.content,
                                            file_name=file_name,
                                            key=f"download_emp_{task['id']}_{comment['id']}"
                                        )
                                        # Thêm dòng này để hiển thị tên file bên dưới
                                        st.caption(f"{file_name}")
                                    except requests.exceptions.RequestException as e:
                                        st.error(f"Không thể tải tệp: {e}")
                    
                    with st.form(key=f"comment_form_{task['id']}", clear_on_submit=True):
                        comment_content = st.text_area("Thêm bình luận của bạn:", key=f"comment_text_{task['id']}", label_visibility="collapsed", placeholder="Nhập trao đổi về công việc...",disabled=is_expired)
                        uploaded_file = st.file_uploader("Đính kèm file (Word, RAR, ZIP <2MB)", type=['doc', 'docx', 'rar', 'zip'], accept_multiple_files=False, key=f"file_{task['id']}",disabled=is_expired)
                        
                        submitted_comment = st.form_submit_button("Gửi bình luận",disabled=is_expired)
                        # =========================================================
                        # Bắt lại nội dung nếu gửi khi hết hạn
                        if submitted_comment and is_expired and (comment_content or uploaded_file):
                            st.warning("⚠️ Nội dung của bạn CHƯA ĐƯỢC GỬI do phiên làm việc đã hết hạn. Dưới đây là bản sao để bạn tiện lưu lại:")
                            if comment_content:
                                # Hiển thị lại nội dung text trong một khung code dễ sao chép
                                st.code(comment_content, language=None)
                            if uploaded_file:
                                st.info(f"Bạn cũng đã đính kèm tệp: **{uploaded_file.name}**. Vui lòng tải lại tệp này sau khi đăng nhập.")
                        # =========================================================
                        if submitted_comment and (comment_content or uploaded_file) and not is_expired:
                            add_comment(task['id'], user.id, comment_content, uploaded_file)
                            st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)