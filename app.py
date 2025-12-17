import os
import uuid
import subprocess
import json
from datetime import datetime
from flask import Flask, request, render_template, redirect, url_for, send_file, session, jsonify
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge


class VideoInfo:
    def __init__(self, duration=0, size=0, bitrate=0, format_name='unknown', 
                 video_codec=None, audio_codec=None, width=None, height=None, fps=None):
        self.duration = duration
        self.size = size
        self.bitrate = bitrate
        self.format_name = format_name
        self.video_codec = video_codec
        self.audio_codec = audio_codec
        self.width = width
        self.height = height
        self.fps = fps
    
    def to_dict(self):
        return {
            'duration': self.duration,
            'size': self.size,
            'bitrate': self.bitrate,
            'format': self.format_name,
            'video_codec': self.video_codec,
            'audio_codec': self.audio_codec,
            'width': self.width,
            'height': self.height,
            'fps': self.fps
        }


class ConversionTask:

    def __init__(self, task_id, input_path, output_path, output_filename, 
                 original_filename, output_format, video_info=None):
        self.task_id = task_id
        self.input_path = input_path
        self.output_path = output_path
        self.output_filename = output_filename
        self.original_filename = original_filename
        self.format = output_format
        self.video_info = video_info
        self.status = 'processing'
        self.error = None
    
    def mark_completed(self):
        self.status = 'completed'
    
    def mark_error(self, error_message):
        self.status = 'error'
        self.error = error_message
    
    def to_dict(self):
        return {
            'status': self.status,
            'input_path': self.input_path,
            'output_path': self.output_path,
            'output_filename': self.output_filename,
            'original_filename': self.original_filename,
            'format': self.format,
            'video_info': self.video_info.to_dict() if self.video_info else None,
            'error': self.error
        }


class TaskManager:
    def __init__(self):
        self.tasks = {}
    
    def create_task(self, task_id, input_path, output_path, output_filename, 
                   original_filename, output_format, video_info=None):
        task = ConversionTask(
            task_id=task_id,
            input_path=input_path,
            output_path=output_path,
            output_filename=output_filename,
            original_filename=original_filename,
            output_format=output_format,
            video_info=video_info
        )
        self.tasks[task_id] = task
        return task
    
    def get_task(self, task_id):
        return self.tasks.get(task_id)
    
    def task_exists(self, task_id):
        return task_id in self.tasks
    
    def delete_task(self, task_id):
        if task_id in self.tasks:
            del self.tasks[task_id]


class VideoConverter:
    OUTPUT_FORMATS = {
        'mp4': {'ext': 'mp4', 'codec': 'libx264', 'audio': 'aac'},
        'webm': {'ext': 'webm', 'codec': 'libvpx-vp9', 'audio': 'libopus'},
        'avi': {'ext': 'avi', 'codec': 'libx264', 'audio': 'mp3'},
        'mkv': {'ext': 'mkv', 'codec': 'libx264', 'audio': 'aac'}
    }
    
    @staticmethod
    def _parse_fps(fps_str):
        try:
            if '/' in fps_str:
                num, den = map(float, fps_str.split('/'))
                return num / den if den != 0 else 0
            return float(fps_str)
        except:
            return None
    
    @classmethod
    def get_video_info(cls, filepath):
        try:
            komanda = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                filepath
            ]
            rezultat = subprocess.run(komanda, capture_output=True, text=True, timeout=10)
            if rezultat.returncode == 0:
                dannye = json.loads(rezultat.stdout)
                video_potok = next((s for s in dannye['streams'] if s['codec_type'] == 'video'), None)
                audio_potok = next((s for s in dannye['streams'] if s['codec_type'] == 'audio'), None)
                
                return VideoInfo(
                    duration=float(dannye['format'].get('duration', 0)),
                    size=int(dannye['format'].get('size', 0)),
                    bitrate=int(dannye['format'].get('bit_rate', 0)),
                    format_name=dannye['format'].get('format_name', 'unknown'),
                    video_codec=video_potok.get('codec_name', 'unknown') if video_potok else None,
                    audio_codec=audio_potok.get('codec_name', 'unknown') if audio_potok else None,
                    width=int(video_potok.get('width', 0)) if video_potok else None,
                    height=int(video_potok.get('height', 0)) if video_potok else None,
                    fps=cls._parse_fps(video_potok.get('r_frame_rate', '0/1')) if video_potok else None
                )
        except Exception as e:
            print(f"Ошибка при получении инфы о видео: {e}")
        return None
    
    @classmethod
    def convert(cls, input_path, output_path, output_format):
        if output_format not in cls.OUTPUT_FORMATS:
            return False, "Неподдерживаемый формат"
        
        format_nastroyki = cls.OUTPUT_FORMATS[output_format]
        
        komanda = [
            'ffmpeg',
            '-i', input_path,
            '-c:v', format_nastroyki['codec'],
            '-c:a', format_nastroyki['audio'],
            '-y',
            output_path
        ]
        
        if output_format == 'webm':
            komanda.insert(-1, '-b:v')
            komanda.insert(-1, '0')
            komanda.insert(-1, '-crf')
            komanda.insert(-1, '30')
        
        try:
            rezultat = subprocess.run(
                komanda,
                capture_output=True,
                text=True,
                timeout=600
            )
            return rezultat.returncode == 0, rezultat.stderr
        except subprocess.TimeoutExpired:
            return False, "Превышено время ожидания конвертации"
        except Exception as e:
            return False, str(e)


class FileValidator:
    def __init__(self, allowed_extensions):
        self.allowed_extensions = allowed_extensions
    
    def is_allowed(self, filename):
        """Проверяет разрешено ли расширение файла"""
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in self.allowed_extensions

app = Flask(__name__)
app.secret_key = 'secret-key-12345'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['CONVERTED_FOLDER'] = 'converted'
app.config['ALLOWED_EXTENSIONS'] = {'mp4', 'avi', 'mov', 'mkv', 'webm'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['CONVERTED_FOLDER'], exist_ok=True)

task_manager = TaskManager()
file_validator = FileValidator(app.config['ALLOWED_EXTENSIONS'])


def process_conversion(task_id, input_path, output_path, output_format):
    try:
        uspekh, soobshchenie_ob_oshibke = VideoConverter.convert(input_path, output_path, output_format)
        
        zadacha = task_manager.get_task(task_id)
        if zadacha:
            if uspekh and os.path.exists(output_path):
                zadacha.mark_completed()
            else:
                zadacha.mark_error(soobshchenie_ob_oshibke or 'Неизвестная ошибка конвертации')
    except Exception as e:
        zadacha = task_manager.get_task(task_id)
        if zadacha:
            zadacha.mark_error(str(e))


@app.route('/')
def index():
    return render_template('index.html', formats=VideoConverter.OUTPUT_FORMATS.keys())


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return redirect(url_for('error', message='Файл не выбран'))
    
    fail = request.files['file']
    format_vyhoda = request.form.get('format', 'mp4')
    
    if fail.filename == '':
        return redirect(url_for('error', message='Файл не выбран'))
    
    if format_vyhoda not in VideoConverter.OUTPUT_FORMATS:
        return redirect(url_for('error', message='Неподдерживаемый формат'))
    
    if not file_validator.is_allowed(fail.filename):
        return redirect(url_for('error', message='Неподдерживаемый формат файла. Разрешенные форматы: MP4, AVI, MOV, MKV, WEBM'))
    
    try:
        unikalnyy_id = str(uuid.uuid4())
        originalnoe_imya = secure_filename(fail.filename)
        rasshirenie = originalnoe_imya.rsplit('.', 1)[1].lower()
        imya_vhodnogo = f"{unikalnyy_id}.{rasshirenie}"
        put_vhodnogo = os.path.join(app.config['UPLOAD_FOLDER'], imya_vhodnogo)
        
        fail.save(put_vhodnogo)
        
        video_informatsiya = VideoConverter.get_video_info(put_vhodnogo)
        
        imya_vyhodnogo = f"{unikalnyy_id}.{VideoConverter.OUTPUT_FORMATS[format_vyhoda]['ext']}"
        put_vyhodnogo = os.path.join(app.config['CONVERTED_FOLDER'], imya_vyhodnogo)
        
        zadacha = task_manager.create_task(
            task_id=unikalnyy_id,
            input_path=put_vhodnogo,
            output_path=put_vyhodnogo,
            output_filename=imya_vyhodnogo,
            original_filename=originalnoe_imya,
            output_format=format_vyhoda,
            video_info=video_informatsiya
        )
        
        import threading
        potok = threading.Thread(
            target=process_conversion,
            args=(unikalnyy_id, put_vhodnogo, put_vyhodnogo, format_vyhoda)
        )
        potok.daemon = True
        potok.start()
        
        session['task_id'] = unikalnyy_id
        
        return redirect(url_for('processing', task_id=unikalnyy_id))
        
    except RequestEntityTooLarge:
        return redirect(url_for('error', message='Файл слишком большой. Максимальный размер: 500 МБ'))
    except Exception as e:
        return redirect(url_for('error', message=f'Ошибка загрузки: {str(e)}'))


@app.route('/processing/<task_id>')
def processing(task_id):
    if not task_manager.task_exists(task_id):
        return redirect(url_for('error', message='Задача не найдена'))
    
    zadacha = task_manager.get_task(task_id)
    return render_template('processing.html', task_id=task_id, task=zadacha.to_dict())


@app.route('/status/<task_id>')
def status(task_id):
    if not task_manager.task_exists(task_id):
        return jsonify({'status': 'not_found'}), 404
    
    zadacha = task_manager.get_task(task_id)
    return jsonify({
        'status': zadacha.status,
        'error': zadacha.error
    })


@app.route('/result/<task_id>')
def result(task_id):
    if not task_manager.task_exists(task_id):
        return redirect(url_for('error', message='Задача не найдена'))
    
    zadacha = task_manager.get_task(task_id)
    
    if zadacha.status == 'error':
        return redirect(url_for('error', message=zadacha.error or 'Ошибка конвертации'))
    
    if zadacha.status != 'completed':
        return redirect(url_for('processing', task_id=task_id))
    
    if not os.path.exists(zadacha.output_path):
        return redirect(url_for('error', message='Файл не найден'))
    
    return render_template('result.html', task=zadacha.to_dict(), task_id=task_id)


@app.route('/download/<task_id>')
def download_file(task_id):
    if not task_manager.task_exists(task_id):
        return redirect(url_for('error', message='Задача не найдена'))
    
    zadacha = task_manager.get_task(task_id)
    
    if not os.path.exists(zadacha.output_path):
        return redirect(url_for('error', message='Файл не найден'))
    
    return send_file(
        zadacha.output_path,
        as_attachment=True,
        download_name=f"{zadacha.original_filename.rsplit('.', 1)[0]}.{VideoConverter.OUTPUT_FORMATS[zadacha.format]['ext']}"
    )


@app.route('/cleanup/<task_id>')
def cleanup(task_id):
    if not task_manager.task_exists(task_id):
        return jsonify({'success': False, 'message': 'Задача не найдена'}), 404
    
    zadacha = task_manager.get_task(task_id)
    
    try:
        if os.path.exists(zadacha.input_path):
            os.remove(zadacha.input_path)
        
        if os.path.exists(zadacha.output_path):
            os.remove(zadacha.output_path)
        
        task_manager.delete_task(task_id)
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/error')
def error():
    soobshchenie = request.args.get('message', 'Произошла неизвестная ошибка')
    return render_template('error.html', message=soobshchenie)


if __name__ == '__main__':
    print("Запуск сервера...")
    print("Убедитесь что FFmpeg установлен!")
    app.run(debug=True, host='0.0.0.0', port=5000)
