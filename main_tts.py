# -*- coding: utf-8 -*-
import sys
import time
import threading
import multiprocessing
import wave
import keyboard
import pyaudio
import os
from datetime import datetime
from openai import OpenAI
import requests
import json
import uuid
import pymysql
from concurrent.futures import ThreadPoolExecutor
import sounddevice as sd
import soundfile as sf
import io
import asyncio
from threading import Thread
import subprocess

class VoiceRecorderCLI:
    def __init__(self):
        self.is_recording = False
        self.audio_frames = []
        self.save_dir = os.path.join(os.getcwd(), "recordings")
        self.tts_dir = os.path.join(os.getcwd(), "tts_audio")
        self.scr_dir = os.path.join(os.getcwd(), "scripts")
        self.DEEPSEEK_API = os.getenv('DEEPSEEK_API', 'None')
        self.STEPFUN_API = os.getenv('STEPFUN_API', 'None')
        self.logid = uuid.uuid4()
        self.input_mode = 'voice'
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.stop_playing_flag = False
        self.current_playing_thread = None
        self.play_queue = []
        self.is_playing = False
        
        # 初始化异步事件循环
        self.event_loop = asyncio.new_event_loop()
        self._init_async_runtime()
        
        # 数据库配置
        self.db_config = {
            "host": "127.0.0.1",
            "user": "root",
            "password": "ttz123",
            "db": "ttz",
            "charset": 'utf8mb4'
        }

        self.init_audio()
        self.setup_hotkey()
        self.prepare_directory()
        self.setup_input_listener()

    def _init_async_runtime(self):
        """启动独立事件循环线程"""
        def loop_runner(loop):
            asyncio.set_event_loop(loop)
            loop.run_forever()

        self.loop_thread = Thread(
            target=loop_runner,
            args=(self.event_loop,),
            daemon=True
        )
        self.loop_thread.start()

    def prepare_directory(self):
        """创建录音文件保存目录"""
        os.makedirs(self.save_dir, exist_ok=True)
        os.makedirs(self.tts_dir, exist_ok=True)
        os.makedirs(self.scr_dir, exist_ok=True)

    def init_audio(self):
        """初始化音频设备"""
        self.audio = pyaudio.PyAudio()
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 16000
        self.chunk = 1024

    def setup_hotkey(self):
        """注册全局快捷键"""
        keyboard.add_hotkey('ctrl+windows', self.toggle_recording)
        keyboard.add_hotkey('alt+windows', self.toggle_input_mode)
        keyboard.add_hotkey('win+esc', self.stop_audio)
        print("全局快捷键已注册: Ctrl + Windows（录音） | Alt + Windows（切换输入模式） | Win+Esc（中断播放）")
    
    def toggle_input_mode(self):
        """切换输入模式"""
        self.input_mode = 'text' if self.input_mode == 'voice' else 'voice'
        mode_desc = "文本输入" if self.input_mode == 'text' else "语音输入"
        print(f"\n输入模式已切换为：{mode_desc}")

    def setup_input_listener(self):
        """优化后的输入监听"""
        def input_listener():
            while True:
                try:
                    if self.input_mode == 'text':
                        user_input = input("\n[文本模式]请输入指令（输入exit返回语音模式）: ")
                        if user_input.lower() == 'exit':
                            self.input_mode = 'voice'
                            print("已切换回语音输入模式")
                            continue
                        self.executor.submit(self.process_text_input, user_input)
                    time.sleep(0.5)
                except Exception as e:
                    print(f"输入监听异常: {str(e)}")
                    time.sleep(1)
                    continue
        threading.Thread(target=input_listener, daemon=True).start()

    def toggle_recording(self):
        """仅在语音模式下响应录音"""
        if self.input_mode == 'voice':
            if not self.is_recording:
                self.start_recording()
            else:
                self.stop_recording()
    
    def start_recording(self):
        """开始录音"""
        try:
            self.audio_frames = []
        
            # 重新初始化stream
            if hasattr(self, 'stream') and self.stream.is_active():
                self.stream.stop_stream()
                self.stream.close()
            self.stream = self.audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.rate,
                input=True,
                frames_per_buffer=self.chunk,
                stream_callback=self.audio_callback,
                start=False
            )
            # 重置停止标志
            self.stop_playing_flag = False
            self.is_recording = True
            self.stream.start_stream()  # 显式启动流
            print("\n录音开始... (再次按下Ctrl+Windows停止)")
        except Exception as e:
            print(f"\n录音启动失败: {str(e)}")
            self.save_error_log("start_recording", str(e))

    def stop_recording(self):
        if hasattr(self, 'stream') and self.stream.is_active():
            try:
                """停止录音并保存文件"""
                self.is_recording = False
                time.sleep(0.1)  # 确保最后一个回调完成
                self.stream.stop_stream()
                self.stream.close()
                print("录音已停止，正在为您处理")
                threading.Timer(0.2, self.save_audio).start()
            except Exception as e:
                print(f"停止录音出错: {str(e)}")
                self.save_error_log("stop_recording", str(e))

    def audio_callback(self, in_data, frame_count, time_info, status):
        """音频采集回调函数"""
        if self.is_recording:
            self.audio_frames.append(in_data)
        return (in_data, pyaudio.paContinue)

    def save_audio(self):
        """异步保存音频并处理"""
        try:
            filename = datetime.now().strftime("recording_%Y%m%d_%H%M%S.wav")
            filepath = os.path.join(self.save_dir, filename)
            self.executor.submit(self.save_audio_file, filepath)
            self.executor.submit(self.process_audio, filepath)
        except Exception as e:
            print(f"处理失败: {str(e)}")
            self.save_error_log("audio_input", str(e))

    def save_audio_file(self, filepath):
        """保存音频文件（独立线程）"""
        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.audio.get_sample_size(self.format))
            wf.setframerate(self.rate)
            wf.writeframes(b''.join(self.audio_frames))
        self.audio_frames = []

    async def async_play_audio(self, text):
        """异步语音合成和播放"""
        client = OpenAI(
            api_key=self.STEPFUN_API,
            base_url="https://api.stepfun.com/v1"
        )
        try:
            speech_file_path = os.path.join(self.tts_dir, datetime.now().strftime("tts_%Y%m%d_%H%M%S.wav"))
            with client.with_streaming_response.audio.speech.create(
                input=text,
                model="step-tts-mini",
                voice="jilingshaonv",
                extra_body={"volume": 1.0}
            ) as response:
                audio_buffer = io.BytesIO()
                if hasattr(response, 'iter_bytes'):
                    for chunk in response.iter_bytes():
                        if self.stop_playing_flag:
                            break
                        audio_buffer.write(chunk)
                elif hasattr(response, 'read'):
                    audio_buffer.write(response.read())
                else:
                    audio_buffer.write(response.content)
                # async for chunk in response.aiter_bytes():
                #     if self.stop_playing_flag:
                #         break
                #     audio_buffer.write(chunk)
                
                if not self.stop_playing_flag:
                    with open(speech_file_path, 'wb') as f:
                      f.write(audio_buffer.getvalue())
                    self.play_queue.append(audio_buffer)
                    self._process_play_queue()

        except Exception as e:
            print(f"语音合成失败: {str(e)}")

    def _process_play_queue(self):
        """处理播放队列"""
        if not self.is_playing and self.play_queue:
            self.is_playing = True
            
            def play_task():
                try:
                    audio_buffer = self.play_queue.pop(0)
                    audio_buffer.seek(0)
                    data, samplerate = sf.read(audio_buffer)
                    sd.play(data, samplerate)
                    sd.wait()
                finally:
                    self.is_playing = False
                    if self.play_queue:
                        self._process_play_queue()
            threading.Thread(target=play_task).start()

    def stop_audio(self):
        """中断音频播放"""
        self.stop_playing_flag = True
        self.play_queue.clear()
        sd.stop()
        print("音频播放已中断")

    def process_audio_response(self, ai_data):
        """统一处理AI响应"""
        # print(ai_data['command'])
        try:
            exec(ai_data['command'])
            print("可执行任务指令已完成。")
        except Exception as e:
            error_msg = f"指令执行错误: {str(e)}"
            print(error_msg)
            sc = f"# -*- coding: utf-8 -*-\n{json_data['command']}"
            with open(f"{self.scr_dir}/{self.logid}.py",'w',encoding='utf-8') as p:
                p.write(sc)
            try:
                subprocess.run(
                    ["python",f"{self.scr_dir}/{self.logid}.py"],
                    capture_output=True,
                    text=True
                    )
            except Exception as e:
                print(e)
            self.save_error_log(ai_data.get('user_input', ''), error_msg)
        
        if ai_data.get('resp_to_user'):
            print(ai_data['resp_to_user'])
            asyncio.run_coroutine_threadsafe(
                self.async_play_audio(ai_data['resp_to_user']),
                self.event_loop
            )

    def process_text_input(self, text):
        """处理文本输入"""
        try:
            res = self.llm_format(text)
            ai_data = json.loads(res)
            ai_data['user_input'] = text
            self.save_audio_log(text, ai_data)
            self.process_audio_response(ai_data)
        except Exception as e:
            print(f"文本处理失败: {str(e)}")

    def process_audio(self, filepath):
        """处理音频转文本"""
        txt = self.sf_asr_to_str(filepath)
        if txt:
            try:
                res = self.llm_format(txt)
                ai_data = json.loads(res)
                ai_data['user_input'] = txt
                self.save_audio_log(txt, ai_data)
                self.process_audio_response(ai_data)
            except Exception as e:
                print(f"语音处理失败: {str(e)}")

    def save_audio_log(self, user_input, ai_response):
        """保存完整日志到数据库"""
        try:
            conn = pymysql.connect(**self.db_config)
            with conn.cursor() as cursor:
                sql = """INSERT INTO audio_logs 
                        (log_id, input_time, user_input, ai_command, user_response, error_info)
                        VALUES (%s, %s, %s, %s, %s, %s)"""
                cursor.execute(sql, (
                    str(self.logid),
                    datetime.now(),
                    user_input,
                    ai_response.get('command'),
                    ai_response.get('resp_to_user'),
                    ai_response.get('uncommand')
                ))
            conn.commit()
        except Exception as e:
            print(f"日志保存失败: {str(e)}")
        finally:
            if 'conn' in locals():
                conn.close()
    
    def save_error_log(self, user_input, error_msg):
        """保存错误日志"""
        try:
            conn = pymysql.connect(**self.db_config)
            with conn.cursor() as cursor:
                sql = """INSERT INTO error_logs 
                        (log_id, error_time, user_input, error_info)
                        VALUES (%s, %s, %s, %s)"""
                cursor.execute(sql, (
                    str(self.logid),
                    datetime.now(),
                    user_input,
                    error_msg
                ))
            conn.commit()
        except Exception as e:
            print(f"错误日志保存失败: {str(e)}")
        finally:
            if 'conn' in locals():
                conn.close()
    
    def sf_asr_to_str(self, audioin):
        """语音识别函数"""
        header = {
            "Authorization": self.STEPFUN_API
        }
        data = {
            "model": "step-asr",
            "response_format": "text"
        }
        files = {
            "file": (audioin, open(audioin, "rb"), "audio/mpeg")
        }
        try:
            response = requests.post(
                url="https://api.stepfun.com/v1/audio/transcriptions",
                headers=header,
                data=data,
                files=files)
            txt = response.text
            print(f"语音识别结果是：\n{txt}")
        except Exception as e:
            print(e)
            txt = ""
        return txt 
    
    def llm_format(self, promt_u):
        """大模型指定格式输出函数"""
        with open('promt.txt','r',encoding='utf8') as f:
            promt_add = f.read()
        client = OpenAI(
            base_url="https://api.deepseek.com/",
            api_key=self.DEEPSEEK_API
        )
        try:
            completion = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {
                        "role": "system",
                        "content": '''
用户通过语音转文字给你提供一段内容，里面可能包含一些可转化为python执行的命令，你需要识别这些指令，并转化为可直接执行的python代码,如果一个命令包含多个子命令，其中包含不可执行部分（缺乏参数），对该子命令代码做注释，确保最大化执行可执行部分；
对用户内容中的非命令部分，使用口语化，情绪饱满，积极阳光，能够给用户提供情绪价值的高情商回答；
最后总结用户的输入，总结以上的处理结果，需要使用更贴近日常交流的语言。格式输出示例：" 
{
"command":"print('helle world')",
"uncommand","用户提到让我帮他买票，我可以给他生成买票的python代码，但是缺少相关参数，无法完成执行。",
"resp_to_user":"主人，我认真聆听了您跟我说的，其中让我帮你查询C盘下happy文件夹的文件数量，已帮你生成可执行代码，按您要求，结果已经帮您放在电脑桌面上，请您查阅；其中有提到需要买票，这个我还没有学会，按照您前面告诉我的，已经给您存到数据库了。小主，还有什么可以为您效劳的。"
}
'''
                    },
                    {
                        "role": "user",
                        "content": f"先阅读用户预置配置信息：{promt_add}，然后处理用户输入：{promt_u}"
                    }
                ],
                temperature=0
            )
            res = completion.choices[0].message.content
        except Exception as e:
            print(e)
            res = None
        return res
    
    def run(self):
        """保持程序运行"""
        print("语音录音器已启动（后台运行）")
        print("使用 Ctrl + Windows 组合键开始/停止录音")
        print("使用 Alt + Windows 组合键切换输入方式")
        print("使用 Esc + Windows 组合键中断播放")
        print("退出请按 Ctrl+C\n")
        keyboard.wait()

if __name__ == '__main__':
    try:
        recorder = VoiceRecorderCLI()
        recorder.run()
    except KeyboardInterrupt:
        print("\n程序已退出")
        sys.exit(0)
