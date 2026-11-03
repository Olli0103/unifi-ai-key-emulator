"""Owned text-plate/TTS/cartoon inputs; never private footage or recognition output.

Generation does not run a tested recognizer, ranker, controller or provider.
Encoded bytes, not regeneration on another host, are the future frozen inputs.
"""
import argparse
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import random
import subprocess
import tempfile
import wave

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from benchmark_fixture_corpus import generate as generate_visual


SENTENCES = (
    'Die blaue Tür ist offen.',
    'Ein rotes Paket steht neben der grünen Kiste.',
    'Der kleine Hund läuft langsam durch den Garten.',
    'Auf dem Tisch liegt ein gelbes Buch.',
    'Die Lampe über dem Fenster leuchtet hell.',
    'Am Morgen singt ein Vogel vor dem Haus.',
)
PLATE_CONDITIONS = ('day','night','glare','rain','motion_blur','occlusion','skew','multi_object')


def save(path, data):
    with Path(path).open('xb') as out:
        os.fchmod(out.fileno(),0o600)
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


def save_json(path, value):
    save(path,(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def plate(text, condition):
    image=Image.new('RGB',(640,320),'#799ba8')
    draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((15,35,625,305),radius=25,fill='#365b85',outline='#173347',width=8)
    draw.rectangle((65,115,575,238),fill='#f2f0e3',outline='#222222',width=5)
    draw.rectangle((70,120,100,233),fill='#2354a2')
    if text is not None:
        font=ImageFont.load_default(size=76)
        draw.text((120,127),text,font=font,fill='#171717')
    if condition == 'night':
        image=image.point(lambda p:round(p*.27))
    elif condition == 'glare':
        mask=Image.new('RGB',image.size,'white')
        alpha=Image.new('L',image.size,0)
        ImageDraw.Draw(alpha).ellipse((245,90,420,260),fill=190)
        image=Image.composite(mask,image,alpha)
    elif condition == 'rain':
        rng=random.Random(672)
        for _ in range(90):
            x,y=rng.randrange(640),rng.randrange(320)
            draw.line((x,y,x-4,y+20),fill='#bdd1dd',width=2)
    elif condition == 'motion_blur':
        image=image.filter(ImageFilter.GaussianBlur(2.6))
    elif condition == 'occlusion':
        draw.rectangle((310,120,345,233),fill='#766957')
    elif condition == 'skew':
        image=image.rotate(8,resample=Image.Resampling.BICUBIC,expand=False,fillcolor='#799ba8')
    elif condition == 'multi_object':
        draw.rectangle((20,25,140,90),fill='#b7965c',outline='#4f3823',width=4)
    return image


def png(image):
    stream=BytesIO()
    image.save(stream,format='PNG')
    return stream.getvalue()


def pcm(samples):
    import struct
    stream=BytesIO()
    with wave.open(stream,'wb') as wav:
        wav.setparams((1,2,16000,len(samples),'NONE','not compressed'))
        wav.writeframes(struct.pack('<'+'h'*len(samples),*samples))
    return stream.getvalue()


def check_audio(raw):
    with wave.open(BytesIO(raw),'rb') as wav:
        if (wav.getnchannels()!=1 or wav.getsampwidth()!=2 or wav.getframerate()!=16000
                or wav.getcomptype()!='NONE' or not 1 <= wav.getnframes() <= 120*16000):
            raise ValueError('Invalid bounded generated PCM')
        samples=wav.readframes(wav.getnframes())
        if len(samples)!=wav.getnframes()*2:
            raise ValueError('Truncated generated PCM')
        return wav.getnframes()/16000


def say(text, *, voice, rate, ffmpeg, temporary):
    message=temporary/'utterance.txt'
    aiff=temporary/'utterance.aiff'
    wav=temporary/'utterance.wav'
    message.write_text(text,encoding='utf-8')
    message.chmod(0o600)
    subprocess.run(['/usr/bin/say','-v',voice,'-r',str(rate),'-f',str(message),'-o',str(aiff)],
                   check=True,capture_output=True,timeout=60)
    subprocess.run([str(ffmpeg),'-nostdin','-hide_banner','-loglevel','error','-y','-i',str(aiff),
                    '-map_metadata','-1','-fflags','+bitexact','-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)],
                   check=True,capture_output=True,timeout=30)
    raw=wav.read_bytes()
    check_audio(raw)
    return raw


def generate(output, *, voice, ffmpeg, render=None):
    if not isinstance(voice,str) or not voice or len(voice)>128 or any(ord(c)<32 for c in voice):
        raise ValueError('Explicit installed voice required')
    root=Path(output)
    root.mkdir(mode=0o700)
    images,audio=root/'plate',root/'speech'
    images.mkdir(mode=0o700)
    audio.mkdir(mode=0o700)
    inputs,gold=[],[]
    rng=random.Random(6731)
    for i,condition in enumerate(PLATE_CONDITIONS):
        text='QA '+''.join(rng.choice('ABCDEFGHJKLMNPRSTUVWXYZ') for _ in range(2))+str(rng.randrange(100,1000))
        data=png(plate(text,condition))
        name=f'plate-{i+1:02}.png'
        save(images/name,data)
        row={'id':f'plate-{i+1:02}','kind':'plate','file_name':'plate/'+name,
             'camera_class':'synthetic-640x320','conditions':[condition], 'input_sha256':digest(data)}
        inputs.append(row)
        gold.append(dict(row,expected=text))
    for i,condition in enumerate(('day','night','glare','rain'),9):
        data=png(plate(None,condition))
        name=f'plate-{i:02}.png'
        save(images/name,data)
        row={'id':f'plate-{i:02}','kind':'plate','file_name':'plate/'+name,
             'camera_class':'synthetic-640x320','conditions':[condition,'no_plate_text'],
             'input_sha256':digest(data)}
        inputs.append(row)
        gold.append(dict(row,expected=''))
    for i,text in enumerate(SENTENCES,1):
        with tempfile.TemporaryDirectory(prefix='owned-tts-',dir=root) as temporary:
            raw=(render(text,i) if render is not None else
                 say(text,voice=voice,rate=220 if i==6 else 165,ffmpeg=ffmpeg,temporary=Path(temporary)))
        if i==5:
            import struct
            with wave.open(BytesIO(raw),'rb') as wav:
                values=struct.unpack('<'+'h'*wav.getnframes(),wav.readframes(wav.getnframes()))
            rms=math.sqrt(sum(v*v for v in values)/len(values))
            noise=random.Random(991)
            amplitude=rms/(10**(18/20))
            raw=pcm([max(-32768,min(32767,round(v+noise.gauss(0,amplitude)))) for v in values])
        duration=check_audio(raw)
        name=f'speech-{i:02}.wav'
        save(audio/name,raw)
        conditions=['tts','german']+(['noise_snr18db'] if i==5 else ['fast'] if i==6 else ['clear'])
        row={'id':f'speech-{i:02}','kind':'speech','file_name':'speech/'+name,
             'camera_class':'synthetic-pcm16k','conditions':conditions,
             'input_sha256':digest(raw),'duration_s':duration}
        inputs.append(row)
        gold.append(dict(row,expected=text))
    for i,condition in enumerate(('silence','noise'),7):
        source=random.Random(9919)
        raw=pcm([0 if condition=='silence' else source.randrange(-1500,1501) for _ in range(3*16000)])
        name=f'speech-{i:02}.wav'
        save(audio/name,raw)
        row={'id':f'speech-{i:02}','kind':'speech','file_name':'speech/'+name,
             'camera_class':'synthetic-pcm16k','conditions':[condition],
             'input_sha256':digest(raw),'duration_s':check_audio(raw)}
        inputs.append(row)
        gold.append(dict(row,expected=''))
    visual=root/'visual'
    generate_visual(visual)
    visual_gold=json.loads((visual/'gold.json').read_text())['cases']
    requests=[('a person outdoors','person'),('a blue car','vehicle'),('a brown cat','cat'),
              ('a dog','dog'),('a cardboard box','package'),('someone near a house','person'),
              ('a purple helicopter',None),('an elephant',None),('a swimming pool',None),('a train',None)]
    index=[{k:row[k] for k in ('case_id','file_name','input_sha256','camera_class','conditions')}
           for row in visual_gold]
    index_sha=digest(json.dumps(index,sort_keys=True,separators=(',',':')).encode())
    for i,(query,subject) in enumerate(requests,1):
        row={'id':f'retrieval-{i:02}','kind':'retrieval','query':query,'k':5,
             'camera_class':'synthetic-outdoor','conditions':['query'],
             'input_sha256':digest(json.dumps({'query':query,'index_sha256':index_sha},sort_keys=True,separators=(',',':')).encode())}
        inputs.append(row)
        gold.append(dict(row,relevant=[r['case_id'] for r in visual_gold if r['subject']==subject]))
    save_json(root/'unlabelled-inputs.json',{'schema':'owned-multimodal-inputs/1','cases':inputs,
              'visual_index':index,'visual_index_sha256':index_sha})
    save_json(root/'gold.json',{'schema':'owned-multimodal-gold/1','cases':gold})
    save_json(root/'generation.json',{'schema':'owned-multimodal-generation/1','synthetic_only':True,
        'generator_sha256':digest(Path(__file__).read_bytes()),'speech_voice':voice,
        'speech_rendering':'injected-test-fixture' if render is not None else 'macos-say-local-file',
        'plate_cases':12,'speech_cases':8,'retrieval_queries':10,'visual_index_images':24,
        'actual_model_measurements':0,'hardware_or_native_acceptance':False,
        'encoded_inputs_only':'No byte-reproducibility across TTS/Pillow/codec versions promised.'})
    return {'synthetic_only':True,'plate_cases':12,'speech_cases':8,'retrieval_queries':10,
            'visual_index_images':24,'actual_model_measurements':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--voice',required=True)
    parser.add_argument('--ffmpeg',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(generate(args.output,voice=args.voice,ffmpeg=args.ffmpeg)))
