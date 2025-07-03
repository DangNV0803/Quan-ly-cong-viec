import streamlit as st
from supabase import create_client, Client
import pandas as pd
from datetime import datetime
from collections import defaultdict
from itertools import groupby
import requests
from zoneinfo import ZoneInfo
import re
import unicodedata

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
        response = supabase.table('tasks').select('*, projects(project_name, id)').eq('assigned_to', user_id).order('due_date', desc=False).execute()
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
    if st.button("Đăng xuất"):
        supabase.auth.sign_out()
        st.session_state.user = None
        st.rerun()
        
    with st.expander("🔑 Đổi mật khẩu"):
        with st.form("change_password_form_emp", clear_on_submit=True):
            new_password = st.text_input("Mật khẩu mới", type="password")
            confirm_password = st.text_input("Xác nhận mật khẩu mới", type="password")
            submitted_pw_change = st.form_submit_button("Lưu mật khẩu mới")

            if submitted_pw_change:
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

        tasks_by_project = defaultdict(list)
        for task in my_tasks:
            project_info = task.get('projects')
            project_name = project_info['project_name'] if project_info and project_info.get('project_name') else "Công việc chung"
            tasks_by_project[project_name].append(task)
        
        sorted_projects = sorted(tasks_by_project.items(), key=lambda item: min(t['due_date'] for t in item[1]))

        for project_name, tasks in sorted_projects:
            st.subheader(f"Dự án: {project_name}")
            for task in tasks:
                comments = fetch_comments(task['id'])
                
                has_new_message = False
                if comments and comments[0]['user_id'] != user.id:
                    has_new_message = True
                
                try:
                    formatted_due_date = datetime.fromisoformat(task['due_date']).astimezone(local_tz).strftime('%d/%m/%Y, %H:%M')
                except (ValueError, TypeError):
                    formatted_due_date = task.get('due_date', 'N/A')

                expander_title = f"**{task['task_name']}** (Hạn: *{formatted_due_date}* | Trạng thái: *{task['status']}*)"
                if has_new_message:
                    expander_title = f"💬 **Mới!** {expander_title}"

                deadline_color = get_deadline_color(task.get('due_date'))

                # Tạo một div có màu nền tương ứng
                st.markdown(
                    f"""
                    <div style="background-color: {deadline_color}; border-radius: 7px; padding: 10px; margin-bottom: 10px;">
                    """,
                    unsafe_allow_html=True
                )

                # Đặt expander vào bên trong div đã được tô màu
                with st.expander(expander_title):
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
                            key=f"status_{task['id']}"
                        )
                        if new_status != task['status']:
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
                                    except requests.exceptions.RequestException as e:
                                        st.error(f"Không thể tải tệp: {e}")
                    
                    with st.form(key=f"comment_form_{task['id']}", clear_on_submit=True):
                        comment_content = st.text_area("Thêm bình luận của bạn:", key=f"comment_text_{task['id']}", label_visibility="collapsed", placeholder="Nhập trao đổi về công việc...")
                        uploaded_file = st.file_uploader("Đính kèm file (Word, <2MB)", type=['doc', 'docx'], accept_multiple_files=False, key=f"file_{task['id']}")
                        
                        submitted_comment = st.form_submit_button("Gửi bình luận")
                        if submitted_comment and (comment_content or uploaded_file):
                            add_comment(task['id'], user.id, comment_content, uploaded_file)
                            st.rerun()
                
                # Đóng thẻ div
                st.markdown("</div>", unsafe_allow_html=True)