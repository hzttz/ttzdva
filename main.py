# -*- coding: utf-8 -*-
import sys
import time
import wave
import keyboard
import pyaudio
import os
import json
import uuid
from datetime import datetime
from openai import AsyncOpenAI
import aiohttp
import aiomysql
import sounddevice as sd
import soundfile as sf
import io
import asyncio
from threading import Thread, Lock
from concurrent.futures import ThreadPoolExecutor

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
        self.play_queue = []
        self.is_playing = False
        self.play_lock = Lock()
        
        # 异步事件循环
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

    async def async_execute(self, coro):
        """安全执行协程"""
        return await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(coro, self.event_loop)
        )

    def prepare_directory(self):
        """创建保存目录"""
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
        """异步输入监听"""
        def input_listener():
            while True:
                try:
                    if self.input_mode == 'text':
                        user_input = input("\n[文本模式]请输入指令（输入exit返回语音模式）: ")
                        if user_input.lower() == 'exit':
                            self.input_mode = 'voice'
                            print("已切换回语音输入模式")
                            continue
                        asyncio.run_coroutine_threadsafe(
                            self.process_text_input(user_input),
                            self.event_loop
                        )
                        print("程序处理中……")
                    time.sleep(0.5)
                except Exception as e:
                    print(f"输入监听异常: {str(e)}")
        Thread(target=input_listener, daemon=True).start()

    def toggle_recording(self):
        """语音模式响应录音"""
        if self.input_mode == 'voice':
            if not self.is_recording:
                self.start_recording()
            else:
                self.stop_recording()

    def reset_recording(self):
        """重置录音状态"""
        self.is_recording = False
        if hasattr(self, 'stream'):
            try:
                self.stream.stop_stream()
                self.stream.close()
            except:
                pass
            del self.stream
        self.audio_frames = []

    def start_recording(self):
        self.reset_recording()
        """开始录音"""
        try:
            self.audio_frames = []
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
            self.stop_playing_flag = False
            self.is_recording = True
            self.stream.start_stream()
            print("\n录音开始... (再次按下Ctrl+Windows停止)")
        except Exception as e:
            print(f"\n录音启动失败: {str(e)}")
            self.async_execute(self.save_error_log("start_recording", str(e)))

    def stop_recording(self):
        """停止录音"""
        if hasattr(self, 'stream') and self.stream.is_active():
            try:
                self.is_recording = False
                time.sleep(0.1)
                self.stream.stop_stream()
                self.stream.close()
                print("录音已停止，正在为您处理")
                Thread(target=self.save_audio).start()
            except Exception as e:
                print(f"停止录音出错: {str(e)}")
                self.async_execute(self.save_error_log("stop_recording", str(e)))

    def audio_callback(self, in_data, frame_count, time_info, status):
        """音频采集回调"""
        if self.is_recording:
            self.audio_frames.append(in_data)
        return (in_data, pyaudio.paContinue)

    def save_audio(self):
        """异步保存处理"""
        try:
            filename = datetime.now().strftime("recording_%Y%m%d_%H%M%S.wav")
            filepath = os.path.join(self.save_dir, filename)
            self.save_audio_file(filepath)
            asyncio.run_coroutine_threadsafe(
                self.process_audio(filepath),
                self.event_loop
            )
        except Exception as e:
            print(f"处理失败: {str(e)}")
            self.async_execute(self.save_error_log("audio_input", str(e)))

    def save_audio_file(self, filepath):
        """同步保存音频文件"""
        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.audio.get_sample_size(self.format))
            wf.setframerate(self.rate)
            wf.writeframes(b''.join(self.audio_frames))
        self.audio_frames = []

    async def async_play_audio(self, text):
        """使用原生aiohttp实现的语音合成方法"""
        try:
            speech_file_path = os.path.join(self.tts_dir, datetime.now().strftime("tts_%Y%m%d_%H%M%S.mp3"))
            
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": self.STEPFUN_API
                }
                
                payload = {
                    "model": "step-tts-mini",
                    "input": text,
                    "voice": "jilingshaonv"  # 可根据需求调整发音人
                }
                
                try:
                    async with session.post(
                        "https://api.stepfun.com/v1/audio/speech",
                        headers=headers,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as response:
                        
                        # 检查HTTP状态码
                        if response.status != 200:
                            error_info = await response.text()
                            print(f"API请求失败: {response.status} - {error_info}")
                            return
                        
                        # 直接读取二进制音频数据
                        audio_data = await response.read()
                        
                        # 保存音频文件
                        with open(speech_file_path, 'wb') as f:
                            f.write(audio_data)
                        
                        # 加入播放队列
                        with self.play_lock:
                            self.play_queue.append(speech_file_path)
                        self._process_play_queue()
                        
                except aiohttp.ClientError as e:
                    print(f"网络请求异常: {str(e)}")
                    await self.save_error_log("network_error", str(e))
                    
        except Exception as e:
            print(f"语音合成失败: {str(e)}")
            await self.save_error_log("tts_error", str(e))


    def _process_play_queue(self):
        """处理播放队列"""
        with self.play_lock:
            if not self.is_playing and self.play_queue:
                self.is_playing = True
                audio_buffer = self.play_queue.pop(0)
                asyncio.run_coroutine_threadsafe(
                    self.play_audio(audio_buffer),
                    self.event_loop
                )

    async def play_audio(self, file_path):
        """改进后的播放方法"""
        try:
            loop = asyncio.get_running_loop()
            
            # 使用soundfile的异步读取
            def sync_read():
                return sf.read(file_path)
            data, samplerate = await loop.run_in_executor(None, sync_read)
            
            # 异步播放控制
            sd.play(data, samplerate)
            while sd.get_stream().active and not self.stop_playing_flag:
                await asyncio.sleep(0.1)
                
            sd.stop()
        except Exception as e:
            print(f"播放失败: {str(e)}")
        finally:
            with self.play_lock:
                self.is_playing = False
                if self.play_queue:
                    self._process_play_queue()


    def stop_audio(self):
        """中断播放"""
        self.stop_playing_flag = True
        with self.play_lock:
            self.play_queue.clear()
        sd.stop()
        print("音频播放已中断")

    async def process_audio_response(self, ai_data):
        """处理AI响应"""
        try:
            exec(ai_data['command'])
            print("指令执行完成")
        except Exception as e:
            error_msg = f"指令错误: {str(e)}"
            print(error_msg)
            await self.save_error_log(ai_data.get('user_input', ''), error_msg)
        
        if ai_data.get('resp_to_user'):
            print(ai_data['resp_to_user'])
            await self.async_play_audio(ai_data['resp_to_user'])

    async def process_text_input(self, text):
        """处理文本输入"""
        try:
            res = await self.llm_format(text)
            ai_data = json.loads(res)
            ai_data['user_input'] = text
            await self.save_audio_log(ai_data)
            await self.process_audio_response(ai_data)
        except Exception as e:
            print(f"文本处理失败: {str(e)}")

    async def process_audio(self, filepath):
        """处理语音输入"""
        txt = await self.sf_asr_to_str(filepath)
        if txt:
            try:
                res = await self.llm_format(txt)
                ai_data = json.loads(res)
                ai_data['user_input'] = txt
                await self.save_audio_log(ai_data)
                await self.process_audio_response(ai_data)
            except Exception as e:
                print(f"语音处理失败: {str(e)}")

    async def sf_asr_to_str(self, audioin):
        """异步语音识别"""
        headers = {"Authorization": self.STEPFUN_API}
        data = {"model": "step-asr", "response_format": "text"}
        
        async with aiohttp.ClientSession() as session:
            form_data = aiohttp.FormData()
            form_data.add_field('file', open(audioin, 'rb'), filename=audioin)
            for k, v in data.items():
                form_data.add_field(k, v)
            
            async with session.post(
                "https://api.stepfun.com/v1/audio/transcriptions",
                headers=headers,
                data=form_data
            ) as response:
                return await response.text()

    async def llm_format(self, prompt_u):
        """异步大模型调用"""
        with open('promt.txt','r',encoding='utf8') as f:
            promt_add = f.read()
        
        headers = {
            "Authorization": f"Bearer {self.DEEPSEEK_API}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": '''
用户通过语音转文字给你提供一段内容，里面可能包含一些可转化为python执行的命令，你需要识别这些指令，并转化为可直接执行的python代码,如果一个命令包含多个子命令，其中包含不可执行部分（缺乏参数），对该子命令代码做注释，确保最大化执行可执行部分；
对用户内容中的非命令部分，使用口语化，情绪饱满，积极阳光，能够给用户提供情绪价值的高情商回答；
最后总结用户的输入，总结以上的处理结果，需要使用更贴近日常交流的语言。严格按以下格式输出，格式输出示例：" 
{
"command":"print('helle world')",
"uncommand","用户提到让我帮他买票，我可以给他生成买票的python代码，但是缺少相关参数，无法完成执行。",
"resp_to_user":"主人，我认真聆听了您跟我说的，其中让我帮你查询C盘下happy文件夹的文件数量，已帮你生成可执行代码，按您要求，结果已经帮您放在电脑桌面上，请您查阅；其中有提到需要买票，这个我还没有学会，按照您前面告诉我的，已经给您存到数据库了。小主，还有什么可以为您效劳的。"
}
'''},  # 保持原有系统提示
                {"role": "user", "content": f"{promt_add}\n用户输入：{prompt_u}"}
            ],
            "temperature": 0
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers=headers,
                json=payload
            ) as response:
                res = await response.json()
                return res['choices'][0]['message']['content']

    async def save_audio_log(self, ai_data):
        """异步保存日志"""
        try:
            conn = await aiomysql.connect(**self.db_config)
            async with conn.cursor() as cursor:
                sql = """INSERT INTO audio_logs 
                        (log_id, input_time, user_input, ai_command, user_response, error_info)
                        VALUES (%s, %s, %s, %s, %s, %s)"""
                await cursor.execute(sql, (
                    str(self.logid),
                    datetime.now(),
                    ai_data.get('user_input'),
                    ai_data.get('command'),
                    ai_data.get('resp_to_user'),
                    ai_data.get('uncommand')
                ))
                await conn.commit()
        except Exception as e:
            print(f"日志保存失败: {str(e)}")
        finally:
            if conn:
                conn.close()

    async def save_error_log(self, user_input, error_msg):
        """异步保存错误日志"""
        try:
            conn = await aiomysql.connect(**self.db_config)
            async with conn.cursor() as cursor:
                sql = """INSERT INTO error_logs 
                        (log_id, error_time, user_input, error_info)
                        VALUES (%s, %s, %s, %s)"""
                await cursor.execute(sql, (
                    str(self.logid),
                    datetime.now(),
                    user_input,
                    error_msg
                ))
                await conn.commit()
        except Exception as e:
            print(f"错误日志保存失败: {str(e)}")
        finally:
            if conn:
                conn.close()

    def run(self):
        """主运行循环"""
        print("语音录音器已启动（后台运行）")
        print("使用 Ctrl + Windows 组合键开始/停止录音")
        print("使用 Alt + Windows 组合键切换输入方式")
        print("使用 Win + Esc 组合键中断播放")
        print("退出请按 Ctrl+C")
        keyboard.wait()

if __name__ == '__main__':
    try:
        recorder = VoiceRecorderCLI()
        recorder.run()
    except KeyboardInterrupt:
        print("\n程序已退出")
        sys.exit(0)
