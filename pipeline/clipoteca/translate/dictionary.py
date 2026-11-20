"""Curated scene concepts with ES/EN/ZH/RU primary translations.

These rows are curated content, not generated translations. Unknown concepts must be
translated by a verified Argos model or reported as translation_failed.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

LANGUAGES = ("es", "en", "zh", "ru")
_RAW = """# clima
lluvia|rain|下雨|дождь
tormenta|storm|风暴|буря
nieve|snow|雪|снег
viento|wind|风|ветер
nube|cloud|云|облако
niebla|fog|雾|туман
granizo|hail|冰雹|град
arcoíris|rainbow|彩虹|радуга
trueno|thunder|雷声|гром
relámpago|lightning|闪电|молния
sol|sun|太阳|солнце
calor|heat|炎热|жара
frío|cold|寒冷|холод
sequía|drought|干旱|засуха
inundación|flood|洪水|наводнение
huracán|hurricane|飓风|ураган
amanecer|sunrise|日出|восход
atardecer|sunset|日落|закат
cielo|sky|天空|небо
escarcha|frost|霜|иней
# naturaleza
bosque|forest|森林|лес
montaña|mountain|山|гора
río|river|河流|река
lago|lake|湖|озеро
océano|ocean|海洋|океан
playa|beach|海滩|пляж
desierto|desert|沙漠|пустыня
selva|jungle|丛林|джунгли
árbol|tree|树|дерево
flor|flower|花|цветок
hoja|leaf|叶子|лист
hierba|grass|草|трава
cascada|waterfall|瀑布|водопад
volcán|volcano|火山|вулкан
glaciar|glacier|冰川|ледник
cueva|cave|洞穴|пещера
isla|island|岛屿|остров
campo|field|田野|поле
jardín|garden|花园|сад
piedra|stone|石头|камень
arena|sand|沙子|песок
fuego|fire|火|огонь
humo|smoke|烟|дым
ola|wave|海浪|волна
acantilado|cliff|悬崖|утёс
# ciudad
ciudad|city|城市|город
calle|street|街道|улица
edificio|building|建筑|здание
puente|bridge|桥|мост
plaza|square|广场|площадь
parque|park|公园|парк
mercado|market|市场|рынок
tráfico|traffic|交通|движение
semáforo|traffic light|红绿灯|светофор
rascacielos|skyscraper|摩天大楼|небоскрёб
estación|station|车站|станция
aeropuerto|airport|机场|аэропорт
puerto|port|港口|порт
túnel|tunnel|隧道|туннель
fábrica|factory|工厂|завод
oficina|office|办公室|офис
hospital|hospital|医院|больница
escuela|school|学校|школа
biblioteca|library|图书馆|библиотека
museo|museum|博物馆|музей
# personas
persona|person|人|человек
familia|family|家庭|семья
niño|child|孩子|ребёнок
mujer|woman|女人|женщина
hombre|man|男人|мужчина
multitud|crowd|人群|толпа
amistad|friendship|友谊|дружба
baile|dance|舞蹈|танец
caminar|walking|步行|ходьба
correr|running|跑步|бег
trabajo|work|工作|работа
conversación|conversation|交谈|разговор
abrazo|hug|拥抱|объятие
sonrisa|smile|微笑|улыбка
retrato|portrait|肖像|портрет
# animales
perro|dog|狗|собака
gato|cat|猫|кошка
caballo|horse|马|лошадь
vaca|cow|牛|корова
oveja|sheep|羊|овца
pájaro|bird|鸟|птица
águila|eagle|鹰|орёл
paloma|pigeon|鸽子|голубь
pez|fish|鱼|рыба
delfín|dolphin|海豚|дельфин
ballena|whale|鲸鱼|кит
tiburón|shark|鲨鱼|акула
león|lion|狮子|лев
tigre|tiger|老虎|тигр
elefante|elephant|大象|слон
mono|monkey|猴子|обезьяна
oso|bear|熊|медведь
zorro|fox|狐狸|лиса
mariposa|butterfly|蝴蝶|бабочка
abeja|bee|蜜蜂|пчела
# transporte
automóvil|car|汽车|автомобиль
camión|truck|卡车|грузовик
autobús|bus|公共汽车|автобус
tren|train|火车|поезд
avión|airplane|飞机|самолёт
barco|ship|船|корабль
bicicleta|bicycle|自行车|велосипед
motocicleta|motorcycle|摩托车|мотоцикл
metro|subway|地铁|метро
helicóptero|helicopter|直升机|вертолёт
taxi|taxi|出租车|такси
tranvía|tram|有轨电车|трамвай
velero|sailboat|帆船|парусник
cohete|rocket|火箭|ракета
tractor|tractor|拖拉机|трактор
# comida
pan|bread|面包|хлеб
arroz|rice|米饭|рис
fruta|fruit|水果|фрукты
manzana|apple|苹果|яблоко
naranja|orange|橙子|апельсин
plátano|banana|香蕉|банан
verdura|vegetable|蔬菜|овощ
tomate|tomato|番茄|помидор
pizza|pizza|披萨|пицца
sopa|soup|汤|суп
café|coffee|咖啡|кофе
té|tea|茶|чай
agua|water|水|вода
leche|milk|牛奶|молоко
chocolate|chocolate|巧克力|шоколад
# tecnología
computadora|computer|电脑|компьютер
teléfono|phone|电话|телефон
robot|robot|机器人|робот
cámara|camera|相机|камера
pantalla|screen|屏幕|экран
internet|internet|互联网|интернет
código|code|代码|код
circuito|circuit|电路|схема
batería|battery|电池|батарея
satélite|satellite|卫星|спутник
dron|drone|无人机|дрон
impresora|printer|打印机|принтер
teclado|keyboard|键盘|клавиатура
microscopio|microscope|显微镜|микроскоп
laboratorio|laboratory|实验室|лаборатория
# espacio
luna|moon|月亮|луна
estrella|star|星星|звезда
planeta|planet|行星|планета
galaxia|galaxy|星系|галактика
universo|universe|宇宙|вселенная
astronauta|astronaut|宇航员|космонавт
meteorito|meteorite|陨石|метеорит
cometa|comet|彗星|комета
telescopio|telescope|望远镜|телескоп
nebulosa|nebula|星云|туманность
# deporte
fútbol|football|足球|футбол
baloncesto|basketball|篮球|баскетбол
tenis|tennis|网球|теннис
natación|swimming|游泳|плавание
ciclismo|cycling|自行车运动|велоспорт
surf|surfing|冲浪|сёрфинг
esquí|skiing|滑雪|лыжный спорт
boxeo|boxing|拳击|бокс
atletismo|athletics|田径|лёгкая атлетика
patinaje|skating|滑冰|катание на коньках
escalada|climbing|攀岩|скалолазание
yoga|yoga|瑜伽|йога
gimnasia|gymnastics|体操|гимнастика
maratón|marathon|马拉松|марафон
béisbol|baseball|棒球|бейсбол
# emociones
alegría|joy|喜悦|радость
tristeza|sadness|悲伤|грусть
miedo|fear|恐惧|страх
sorpresa|surprise|惊讶|удивление
amor|love|爱|любовь
paz|peace|和平|мир
esperanza|hope|希望|надежда
soledad|loneliness|孤独|одиночество
calma|calm|平静|спокойствие
entusiasmo|enthusiasm|热情|энтузиазм
ira|anger|愤怒|гнев
nostalgia|nostalgia|怀旧|ностальгия
curiosidad|curiosity|好奇心|любопытство
confianza|trust|信任|доверие
orgullo|pride|自豪|гордость
# arquitectura
casa|house|房子|дом
apartamento|apartment|公寓|квартира
castillo|castle|城堡|замок
iglesia|church|教堂|церковь
templo|temple|寺庙|храм
estadio|stadium|体育场|стадион
teatro|theater|剧院|театр
catedral|cathedral|大教堂|собор
monumento|monument|纪念碑|памятник
escalera|stairs|楼梯|лестница
ventana|window|窗户|окно
puerta|door|门|дверь
balcón|balcony|阳台|балкон
techo|roof|屋顶|крыша
fachada|facade|立面|фасад"""


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


@dataclass(frozen=True)
class SceneConcept:
    category: str
    terms: dict[str, str]


def _build() -> tuple[SceneConcept, ...]:
    rows: list[SceneConcept] = []
    category = ""
    for line in _RAW.splitlines():
        if not line:
            continue
        if line.startswith("# "):
            category = line[2:]
            continue
        values = line.split("|")
        if len(values) != 4 or not all(values):
            raise ValueError(f"invalid curated translation row: {line}")
        rows.append(SceneConcept(category, dict(zip(LANGUAGES, values, strict=True))))
    return tuple(rows)


CONCEPTS = _build()
LOOKUP = {normalize(term): concept for concept in CONCEPTS for term in concept.terms.values()}
SYNONYMS = {
    "lluvia": {
        "es": ["lluvia fuerte"],
        "en": ["heavy rain"],
        "zh": ["暴雨"],
        "ru": ["сильный дождь"],
    },
}


def find_concept(term: str) -> SceneConcept | None:
    return LOOKUP.get(normalize(term))
