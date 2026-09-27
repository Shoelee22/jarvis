"""Offline joke bank (Phase 7 dynamic provider).

A hand-written, family-friendly bank of 260 jokes across 10 categories.
No network, no placeholders -- every entry is a real joke.

Addressable tools:
    joke.random            -- random joke, optional {"category": str}
    joke.<category>        -- random joke from one of the 10 categories
    joke.<joke_id>         -- the exact joke with that id (j001..j260)

Handler contract: dict in -> dict out, never raises.
"""
from __future__ import annotations

import random

from ..base import Tool
from .providers import Provider

# ---------------------------------------------------------------------------
# The joke bank: {"id": "j001".., "category": str, "text": str}
# ---------------------------------------------------------------------------

JOKES: list[dict] = [
    # --- tech (j001-j026) ---
    {"id": "j001", "category": "tech", "text": "Why did the smartphone go to therapy? It had too many unresolved notifications."},
    {"id": "j002", "category": "tech", "text": "My computer's memory is like my memory: it forgets everything by morning."},
    {"id": "j003", "category": "tech", "text": "The WiFi went down at home, so I had to talk to my family. They seem like nice people."},
    {"id": "j004", "category": "tech", "text": "I told my router a joke. It didn't get it. The signal was too weak."},
    {"id": "j005", "category": "tech", "text": "The cloud is just someone else's computer that you trust with your most embarrassing photos."},
    {"id": "j006", "category": "tech", "text": "My phone's battery lasts longer than my motivation."},
    {"id": "j007", "category": "tech", "text": "I changed my password to 'incorrect', so when I forget it the computer tells me: your password is incorrect."},
    {"id": "j008", "category": "tech", "text": "Bluetooth headphones are wonderful, until they pair with the neighbor's TV on movie night."},
    {"id": "j009", "category": "tech", "text": "I have a love-hate relationship with autocorrect. It always ducks things up."},
    {"id": "j010", "category": "tech", "text": "My smart TV is so smart it silently judges my streaming choices."},
    {"id": "j011", "category": "tech", "text": "The office printer is the only device that can smell fear."},
    {"id": "j012", "category": "tech", "text": "I asked my voice assistant to play dead. It played the sad trombone sound instead."},
    {"id": "j013", "category": "tech", "text": "My GPS rerouted me through my feelings. It took forever."},
    {"id": "j014", "category": "tech", "text": "Caps lock is cruise control for cool."},
    {"id": "j015", "category": "tech", "text": "I don't need a personal assistant. I need a personal IT department."},
    {"id": "j016", "category": "tech", "text": "My laptop fan spins so loudly it could power a small village."},
    {"id": "j017", "category": "tech", "text": "Cloud storage: where files go to be synced into oblivion."},
    {"id": "j018", "category": "tech", "text": "I named my WiFi 'Pretty Fly for a WiFi'. Now the neighbors keep connecting just to steal the joke."},
    {"id": "j019", "category": "tech", "text": "My screen time report should legally come with a therapist referral."},
    {"id": "j020", "category": "tech", "text": "I tried to photograph fog. Mist."},
    {"id": "j021", "category": "tech", "text": "My headphones died mid-workout, so I had to listen to my own thoughts. Zero stars."},
    {"id": "j022", "category": "tech", "text": "I love my new standing desk. I stand, I sit, I stand. Mostly I just carry the laptop around the house."},
    {"id": "j023", "category": "tech", "text": "The delete key is my favorite time machine."},
    {"id": "j024", "category": "tech", "text": "I asked an AI to write my bio. It said: human, probably."},
    {"id": "j025", "category": "tech", "text": "My inbox has 4,000 unread emails. At this point they're just pen pals I never reply to."},
    {"id": "j026", "category": "tech", "text": "Software update available: 45 minutes of your life you will never get back."},
]

JOKES += [
    # --- programming (j027-j052) ---
    {"id": "j027", "category": "programming", "text": "Why do programmers prefer dark mode? Because light attracts bugs."},
    {"id": "j028", "category": "programming", "text": "A SQL query walks into a bar, sees two tables, and asks: mind if I join you?"},
    {"id": "j029", "category": "programming", "text": "There are only 10 kinds of people: those who understand binary and those who do not."},
    {"id": "j030", "category": "programming", "text": "Why did the programmer quit his job? He did not get arrays."},
    {"id": "j031", "category": "programming", "text": "Debugging is like being the detective in a crime movie where you are also the murderer."},
    {"id": "j032", "category": "programming", "text": "A programmer's wife says: get a loaf of bread. If they have eggs, get a dozen. He comes home with twelve loaves of bread."},
    {"id": "j033", "category": "programming", "text": "Real programmers count from 0."},
    {"id": "j034", "category": "programming", "text": "Why do Java developers wear glasses? Because they do not C-sharp."},
    {"id": "j035", "category": "programming", "text": "My code has no bugs. It just develops random features."},
    {"id": "j036", "category": "programming", "text": "I would tell you a joke about UDP, but you might not get it."},
    {"id": "j037", "category": "programming", "text": "I would tell you a joke about TCP, but I would have to keep repeating it until you got it."},
    {"id": "j038", "category": "programming", "text": "99 little bugs in the code, 99 little bugs. Take one down, patch it around: 117 little bugs in the code."},
    {"id": "j039", "category": "programming", "text": "Why did the developer go broke? He used up all his cache."},
    {"id": "j040", "category": "programming", "text": "A programmer keeps two glasses on the nightstand: one full of water in case he gets thirsty, one empty in case he does not."},
    {"id": "j041", "category": "programming", "text": "'It works on my machine' is the programmer's version of 'the dog ate my homework'."},
    {"id": "j042", "category": "programming", "text": "Python programmers wear snake boots. It matches their imports."},
    {"id": "j043", "category": "programming", "text": "Why do programmers confuse Halloween and Christmas? Because OCT 31 equals DEC 25."},
    {"id": "j044", "category": "programming", "text": "Typical git history: fixed stuff, actually fixed stuff, okay for real this time."},
    {"id": "j045", "category": "programming", "text": "My code review feedback: this works, but I am disappointed in you."},
    {"id": "j046", "category": "programming", "text": "Recursion: see recursion."},
    {"id": "j047", "category": "programming", "text": "Why did the function break up with the loop? Too much iteration, not enough commitment."},
    {"id": "j048", "category": "programming", "text": "CSS is easy. It is like riding a bike, which is on fire, in a tornado."},
    {"id": "j049", "category": "programming", "text": "There are two hard problems in computer science: cache invalidation, naming things, and off-by-one errors."},
    {"id": "j050", "category": "programming", "text": "Why do programmers hate nature? Too many bugs."},
    {"id": "j051", "category": "programming", "text": "A semicolon walks into a bar. The bartender says, we do not serve your type here."},
    {"id": "j052", "category": "programming", "text": "Programming is 10 percent writing code and 90 percent figuring out why it does not work."},
]

JOKES += [
    # --- desi (j053-j078) ---
    {"id": "j053", "category": "desi", "text": "In India, '5 minutes' means anything from five minutes to tomorrow."},
    {"id": "j054", "category": "desi", "text": "Indian moms have a PhD in finding things. 'Did you check properly? Let me come.' Finds it in three seconds."},
    {"id": "j055", "category": "desi", "text": "Sharma ji ka beta topped again. He remains India's most successful fictional character."},
    {"id": "j056", "category": "desi", "text": "An Indian wedding has two functions: the wedding, and the relatives discussing everyone else's wedding."},
    {"id": "j057", "category": "desi", "text": "'Beta, engineering kar lo, scope hai.' Every Indian parent, since 1995."},
    {"id": "j058", "category": "desi", "text": "Indian traffic rule number one: honk first, think later."},
    {"id": "j059", "category": "desi", "text": "In an Indian house, the TV remote has a designated owner: whoever is watching the serial."},
    {"id": "j060", "category": "desi", "text": "Indian moms can tell the exact temperature of food by touching the vessel for half a second."},
    {"id": "j061", "category": "desi", "text": "In India we never say 'I am full'. We say 'bas ek aur roti'."},
    {"id": "j062", "category": "desi", "text": "The monsoon does not start when it rains. It starts when your mom shouts 'kapde andar le lo!'"},
    {"id": "j063", "category": "desi", "text": "Indian dad's financial advice, free of charge: FD kara lo, safe hai."},
    {"id": "j064", "category": "desi", "text": "An Indian mom's cure for everything: haldi doodh."},
    {"id": "j065", "category": "desi", "text": "In India, guests arriving takes ten minutes. Guests leaving takes two hours."},
    {"id": "j066", "category": "desi", "text": "'Log kya kahenge' has controlled more life decisions in India than any government."},
    {"id": "j067", "category": "desi", "text": "Indian trains never run late. They arrive fashionably late."},
    {"id": "j068", "category": "desi", "text": "The most common WiFi password in India is written on a piece of paper stuck to the wall."},
    {"id": "j069", "category": "desi", "text": "An Indian mom packing tiffin: 'Khana khatm ho gaya? Dabba khali mat bhejo.'"},
    {"id": "j070", "category": "desi", "text": "In India, 'thoda adjust kar lo' has resolved more conflicts than the United Nations."},
    {"id": "j071", "category": "desi", "text": "An Indian dad at a restaurant calculates the bill faster than the waiter."},
    {"id": "j072", "category": "desi", "text": "Cricket in India is not a sport. It is a festival with 1.4 billion selectors."},
    {"id": "j073", "category": "desi", "text": "'Beta, phone side me rakho, khana khao,' says mom, then spends twenty minutes on a video call."},
    {"id": "j074", "category": "desi", "text": "Indian moms have perfect aim with a flying chappal. Scientists are still studying the physics."},
    {"id": "j075", "category": "desi", "text": "An Indian dad's love language: filling your car's fuel tank without telling you."},
    {"id": "j076", "category": "desi", "text": "In India, 'free' is the most powerful marketing word ever invented."},
    {"id": "j077", "category": "desi", "text": "The Indian headshake can mean yes, no, maybe, and 'I have no idea', all at the same time."},
    {"id": "j078", "category": "desi", "text": "The average Indian parent's retirement plan: beta."},
]

JOKES += [
    # --- dad (j079-j104) ---
    {"id": "j079", "category": "dad", "text": "I used to hate facial hair, but then it grew on me."},
    {"id": "j080", "category": "dad", "text": "What do you call a fake noodle? An impasta."},
    {"id": "j081", "category": "dad", "text": "I only know 25 letters of the alphabet. I don't know y."},
    {"id": "j082", "category": "dad", "text": "Why don't scientists trust atoms? Because they make up everything."},
    {"id": "j083", "category": "dad", "text": "I told my wife she was drawing her eyebrows too high. She looked surprised."},
    {"id": "j084", "category": "dad", "text": "What do you call cheese that is not yours? Nacho cheese."},
    {"id": "j085", "category": "dad", "text": "My boss told me to have a good day, so I went home."},
    {"id": "j086", "category": "dad", "text": "Why did the scarecrow win an award? Because he was outstanding in his field."},
    {"id": "j087", "category": "dad", "text": "I used to play piano by ear, but now I use my hands."},
    {"id": "j088", "category": "dad", "text": "What do you call a fish without eyes? A fsh."},
    {"id": "j089", "category": "dad", "text": "I am reading a book about anti-gravity. It is impossible to put down."},
    {"id": "j090", "category": "dad", "text": "Did you hear about the restaurant on the moon? Great food, no atmosphere."},
    {"id": "j091", "category": "dad", "text": "Why don't skeletons fight each other? They don't have the guts."},
    {"id": "j092", "category": "dad", "text": "I told my dad I was hungry, so he said: Hi hungry, I am dad."},
    {"id": "j093", "category": "dad", "text": "What do you call a sleeping bull? A bulldozer."},
    {"id": "j094", "category": "dad", "text": "I wanted to tell a time-travel joke, but you did not like it yet."},
    {"id": "j095", "category": "dad", "text": "What do you get when you cross a snowman and a vampire? Frostbite."},
    {"id": "j096", "category": "dad", "text": "I used to be a banker, but I lost interest."},
    {"id": "j097", "category": "dad", "text": "Why can't your nose be 12 inches long? Because then it would be a foot."},
    {"id": "j098", "category": "dad", "text": "I ordered a chicken and an egg online. I will let you know which arrives first."},
    {"id": "j099", "category": "dad", "text": "What do you call a can opener that does not work? A can't opener."},
    {"id": "j100", "category": "dad", "text": "I am afraid for the calendar. Its days are numbered."},
    {"id": "j101", "category": "dad", "text": "Why did the cookie go to the doctor? Because it felt crumby."},
    {"id": "j102", "category": "dad", "text": "What did the ocean say to the beach? Nothing, it just waved."},
    {"id": "j103", "category": "dad", "text": "I don't trust stairs. They are always up to something."},
    {"id": "j104", "category": "dad", "text": "Why did the math book look so sad? It had too many problems."},
]

JOKES += [
    # --- oneliners (j105-j130) ---
    {"id": "j105", "category": "oneliners", "text": "I am not lazy. I am on energy-saving mode."},
    {"id": "j106", "category": "oneliners", "text": "I need a six-month vacation, twice a year."},
    {"id": "j107", "category": "oneliners", "text": "My wallet is like an onion: opening it makes me cry."},
    {"id": "j108", "category": "oneliners", "text": "I followed my heart, and it led me to the fridge."},
    {"id": "j109", "category": "oneliners", "text": "Adults are just kids with credit card debt."},
    {"id": "j110", "category": "oneliners", "text": "I am not arguing. I am just explaining why I am right."},
    {"id": "j111", "category": "oneliners", "text": "Common sense is not that common."},
    {"id": "j112", "category": "oneliners", "text": "I put the pro in procrastination."},
    {"id": "j113", "category": "oneliners", "text": "My bed is a magical place where I suddenly remember everything I forgot to do."},
    {"id": "j114", "category": "oneliners", "text": "Silence is golden. Duct tape is silver."},
    {"id": "j115", "category": "oneliners", "text": "I am on a seafood diet. I see food, and I eat it."},
    {"id": "j116", "category": "oneliners", "text": "Money cannot buy happiness, but it can buy pizza, and that is basically the same thing."},
    {"id": "j117", "category": "oneliners", "text": "I run on caffeine, chaos, and questionable decisions."},
    {"id": "j118", "category": "oneliners", "text": "My house was clean yesterday. Sorry you missed it."},
    {"id": "j119", "category": "oneliners", "text": "I do not suffer from insanity. I enjoy every minute of it."},
    {"id": "j120", "category": "oneliners", "text": "Life is short. Smile while you still have teeth."},
    {"id": "j121", "category": "oneliners", "text": "I am not short. I am concentrated awesome."},
    {"id": "j122", "category": "oneliners", "text": "If at first you do not succeed, skydiving is not for you."},
    {"id": "j123", "category": "oneliners", "text": "Behind every great man is a woman rolling her eyes."},
    {"id": "j124", "category": "oneliners", "text": "I have a lot of jokes about unemployed people, but none of them work."},
    {"id": "j125", "category": "oneliners", "text": "I used to think I was indecisive. Now I am not so sure."},
    {"id": "j126", "category": "oneliners", "text": "The early bird gets the worm, but the second mouse gets the cheese."},
    {"id": "j127", "category": "oneliners", "text": "I am not clumsy. The floor just hates me."},
    {"id": "j128", "category": "oneliners", "text": "A balanced diet is a cookie in each hand."},
    {"id": "j129", "category": "oneliners", "text": "I told myself I should stop drinking coffee, but I am not a quitter."},
    {"id": "j130", "category": "oneliners", "text": "My tolerance for idiots is extremely low these days. I used to have some immunity, but I built up a resistance."},
]

JOKES += [
    # --- science (j131-j156) ---
    {"id": "j131", "category": "science", "text": "Never trust an atom. They make up everything."},
    {"id": "j132", "category": "science", "text": "I wanted to tell a chemistry joke, but all the good ones argon."},
    {"id": "j133", "category": "science", "text": "The mitochondria is the powerhouse of the cell, and of every biology exam."},
    {"id": "j134", "category": "science", "text": "Why did the physics student break up with the biology student? There was no chemistry."},
    {"id": "j135", "category": "science", "text": "A photon checks into a hotel. The bellhop asks if it has luggage. It says: no, I am traveling light."},
    {"id": "j136", "category": "science", "text": "Biology is just applied chemistry. Chemistry is just applied physics. Physics is just applied math."},
    {"id": "j137", "category": "science", "text": "I have a new theory on inertia, but I am waiting for it to gain momentum."},
    {"id": "j138", "category": "science", "text": "Helium walks into a bar. The bartender says: we do not serve noble gases here. Helium does not react."},
    {"id": "j139", "category": "science", "text": "The speed of light is fast, but have you seen me run to the fridge at midnight?"},
    {"id": "j140", "category": "science", "text": "Why are chemists great at solving problems? They have all the solutions."},
    {"id": "j141", "category": "science", "text": "My physics teacher said I have potential. Then I dropped."},
    {"id": "j142", "category": "science", "text": "A neutron walks into a bar and asks how much for a drink. The bartender says: for you, no charge."},
    {"id": "j143", "category": "science", "text": "Why did the amoeba need some space? It was time to divide."},
    {"id": "j144", "category": "science", "text": "Geology rocks, but geography is where it is at."},
    {"id": "j145", "category": "science", "text": "I told a joke about the periodic table. It got a mixed reaction."},
    {"id": "j146", "category": "science", "text": "Why do astronomers never get lost? They always follow the stars."},
    {"id": "j147", "category": "science", "text": "Oxygen and magnesium got together. OMg."},
    {"id": "j148", "category": "science", "text": "The law of conservation of energy: my energy is conserved by doing nothing."},
    {"id": "j149", "category": "science", "text": "Why did the scientist install a knocker on his door? He wanted to win the Nobel Prize."},
    {"id": "j150", "category": "science", "text": "Entropy is not what it used to be."},
    {"id": "j151", "category": "science", "text": "I have a joke about DNA, but it might be too twisted."},
    {"id": "j152", "category": "science", "text": "Why did the biologist become a gardener? He wanted to study plant cells up close."},
    {"id": "j153", "category": "science", "text": "A physicist, an engineer, and an economist walk into a bar. The bartender says: this must be some kind of joke."},
    {"id": "j154", "category": "science", "text": "Why can't you trust a chemist's advice? They will just mix things up."},
    {"id": "j155", "category": "science", "text": "Gravity is just a theory, said no one falling down the stairs."},
    {"id": "j156", "category": "science", "text": "My lab partner and I make great chemistry. The experiments, however, are a disaster."},
]

JOKES += [
    # --- food (j157-j182) ---
    {"id": "j157", "category": "food", "text": "Lettuce turnip the beet."},
    {"id": "j158", "category": "food", "text": "I am on a roll. A dinner roll."},
    {"id": "j159", "category": "food", "text": "You are the apple of my pie."},
    {"id": "j160", "category": "food", "text": "Donut worry, be happy."},
    {"id": "j161", "category": "food", "text": "I am soy into you."},
    {"id": "j162", "category": "food", "text": "Olive you so much."},
    {"id": "j163", "category": "food", "text": "We make a great pear."},
    {"id": "j164", "category": "food", "text": "You are one in a melon."},
    {"id": "j165", "category": "food", "text": "I am bananas for you."},
    {"id": "j166", "category": "food", "text": "Life is gouda."},
    {"id": "j167", "category": "food", "text": "You have a pizza my heart."},
    {"id": "j168", "category": "food", "text": "I love you from my head tomatoes."},
    {"id": "j169", "category": "food", "text": "Whisk me away."},
    {"id": "j170", "category": "food", "text": "You are the peanut butter to my jelly."},
    {"id": "j171", "category": "food", "text": "I am nacho average friend."},
    {"id": "j172", "category": "food", "text": "Everything is brew-tiful with coffee."},
    {"id": "j173", "category": "food", "text": "Espresso yourself."},
    {"id": "j174", "category": "food", "text": "I like you a latte."},
    {"id": "j175", "category": "food", "text": "Lettuce romaine friends forever."},
    {"id": "j176", "category": "food", "text": "You are egg-cellent."},
    {"id": "j177", "category": "food", "text": "I relish our friendship."},
    {"id": "j178", "category": "food", "text": "It is nacho problem, it is mine."},
    {"id": "j179", "category": "food", "text": "Butter late than never."},
    {"id": "j180", "category": "food", "text": "I donut care what anyone thinks."},
    {"id": "j181", "category": "food", "text": "You are tea-rific."},
    {"id": "j182", "category": "food", "text": "I yam what I yam, and I yam hungry."},
]

JOKES += [
    # --- work (j183-j208) ---
    {"id": "j183", "category": "work", "text": "I love deadlines. I love the whooshing sound they make as they fly by."},
    {"id": "j184", "category": "work", "text": "The closest I get to a promotion is when my chair gets taller."},
    {"id": "j185", "category": "work", "text": "Work hard in silence. Let your nap be your noise."},
    {"id": "j186", "category": "work", "text": "My job is secure. Nobody else wants it."},
    {"id": "j187", "category": "work", "text": "I asked for a raise and got a standing ovation instead."},
    {"id": "j188", "category": "work", "text": "Meetings: where minutes are kept and hours are lost."},
    {"id": "j189", "category": "work", "text": "I have a great work-life balance. I work, and I have no life."},
    {"id": "j190", "category": "work", "text": "The only thing I bring to the table is my lunch."},
    {"id": "j191", "category": "work", "text": "I am not a morning person or a night person. I am a leave-me-alone person."},
    {"id": "j192", "category": "work", "text": "My resume says I am a team player. My team says I am a player."},
    {"id": "j193", "category": "work", "text": "The office coffee is so strong it files its own HR complaints."},
    {"id": "j194", "category": "work", "text": "Fridays are my second favorite F word."},
    {"id": "j195", "category": "work", "text": "I work for money. If you want loyalty, get a dog."},
    {"id": "j196", "category": "work", "text": "My computer screen is the only window I look out of."},
    {"id": "j197", "category": "work", "text": "I am great at multitasking. I can waste time, be unproductive, and procrastinate all at once."},
    {"id": "j198", "category": "work", "text": "The break room microwave has seen things."},
    {"id": "j199", "category": "work", "text": "I survived another meeting that could have been an email."},
    {"id": "j200", "category": "work", "text": "Teamwork makes the dream work. Unless it is Monday."},
    {"id": "j201", "category": "work", "text": "I am not late. I am on flexible time."},
    {"id": "j202", "category": "work", "text": "The printer jammed again. I have named it Kevin. Kevin and I are no longer friends."},
    {"id": "j203", "category": "work", "text": "I love my job. It is the work I cannot stand."},
    {"id": "j204", "category": "work", "text": "I asked my boss for a raise. He said money cannot buy happiness. I said, then why do you take a salary?"},
    {"id": "j205", "category": "work", "text": "My to-do list is just a list of things I will feel guilty about tomorrow."},
    {"id": "j206", "category": "work", "text": "I told HR I needed a mental health day. They said: take the week, we noticed."},
    {"id": "j207", "category": "work", "text": "Hard work pays off in the future. Laziness pays off right now."},
    {"id": "j208", "category": "work", "text": "I put in my two weeks notice. My boss said: but you have only been here three days. I said: exactly."},
]

JOKES += [
    # --- animals (j209-j234) ---
    {"id": "j209", "category": "animals", "text": "What do you call a fish with no eyes? A fsh."},
    {"id": "j210", "category": "animals", "text": "Why don't elephants use computers? They are afraid of the mouse."},
    {"id": "j211", "category": "animals", "text": "What do you call a bear with no teeth? A gummy bear."},
    {"id": "j212", "category": "animals", "text": "Why did the chicken join a band? Because it had the drumsticks."},
    {"id": "j213", "category": "animals", "text": "What do you call a dog that does magic tricks? A labracadabrador."},
    {"id": "j214", "category": "animals", "text": "Why are cats bad storytellers? They only have one tail."},
    {"id": "j215", "category": "animals", "text": "What do you call a lazy kangaroo? A pouch potato."},
    {"id": "j216", "category": "animals", "text": "Why did the cow go to space? To see the moooon."},
    {"id": "j217", "category": "animals", "text": "What do you get when you cross a snake and a pie? A pie-thon."},
    {"id": "j218", "category": "animals", "text": "Why don't sharks eat clowns? They taste funny."},
    {"id": "j219", "category": "animals", "text": "What do you call an alligator in a vest? An investigator."},
    {"id": "j220", "category": "animals", "text": "Why did the bird go to the hospital? To get tweetment."},
    {"id": "j221", "category": "animals", "text": "What do you call a pig that does karate? A pork chop."},
    {"id": "j222", "category": "animals", "text": "Why are frogs so happy? They eat whatever bugs them."},
    {"id": "j223", "category": "animals", "text": "What do you call a horse that lives next door? A neigh-bor."},
    {"id": "j224", "category": "animals", "text": "Why did the turtle cross the road? To get to the shell station."},
    {"id": "j225", "category": "animals", "text": "What do you call a cat that drinks lemonade? A sour puss."},
    {"id": "j226", "category": "animals", "text": "Why do bees have sticky hair? Because they use honeycombs."},
    {"id": "j227", "category": "animals", "text": "What do you call a deer with no eyes? No eye deer."},
    {"id": "j228", "category": "animals", "text": "Why did the octopus blush? It saw the bottom of the ocean."},
    {"id": "j229", "category": "animals", "text": "What do you call a fish that practices medicine? A sturgeon."},
    {"id": "j230", "category": "animals", "text": "Why are dogs such good dancers? They have all the right mooves."},
    {"id": "j231", "category": "animals", "text": "Why don't cats play poker in the jungle? Too many cheetahs."},
    {"id": "j232", "category": "animals", "text": "Why did the lion get lost? Because the jungle was too mane-stream."},
    {"id": "j233", "category": "animals", "text": "What do you call a rabbit with fleas? Bugs Bunny."},
    {"id": "j234", "category": "animals", "text": "Why did the sheep get a haircut? It was feeling a little sheepish about the split ends."},
]

JOKES += [
    # --- school (j235-j260) ---
    {"id": "j235", "category": "school", "text": "Why did the student eat his homework? Because the teacher said it was a piece of cake."},
    {"id": "j236", "category": "school", "text": "Why is 6 afraid of 7? Because 7 8 9."},
    {"id": "j237", "category": "school", "text": "The teacher said: name two pronouns. The student said: who, me?"},
    {"id": "j238", "category": "school", "text": "Why was the equal sign so humble? It knew it was not less than or greater than anyone else."},
    {"id": "j239", "category": "school", "text": "What is a math teacher's favorite dessert? Pi."},
    {"id": "j240", "category": "school", "text": "Why did the student bring a ladder to school? He wanted to go to high school."},
    {"id": "j241", "category": "school", "text": "What did the zero say to the eight? Nice belt."},
    {"id": "j242", "category": "school", "text": "Why was the geometry book always unhappy? It had too many angles."},
    {"id": "j243", "category": "school", "text": "Why did the kid study on an airplane? He wanted a higher education."},
    {"id": "j244", "category": "school", "text": "Why did the music teacher need a ladder? To reach the high notes."},
    {"id": "j245", "category": "school", "text": "Why are obtuse angles so depressed? They are never right."},
    {"id": "j246", "category": "school", "text": "What did the pen say to the pencil? You are pointless."},
    {"id": "j247", "category": "school", "text": "Why did the student put his money in the freezer? He wanted cold hard cash."},
    {"id": "j248", "category": "school", "text": "Why did the library book blush? It saw the checkout counter."},
    {"id": "j249", "category": "school", "text": "What is the most tired part of a school? The calendar. It has the most days off."},
    {"id": "j250", "category": "school", "text": "Why did the student bring string to class? To tie up loose ends."},
    {"id": "j251", "category": "school", "text": "The teacher asked: if I had ten apples and you took four, what would you have? The student said: detention."},
    {"id": "j252", "category": "school", "text": "Why was the broom late to school? It overswept."},
    {"id": "j253", "category": "school", "text": "Why did the clock get detention? It was ticking everyone off."},
    {"id": "j254", "category": "school", "text": "Why did the student throw the clock out the window? He wanted to see time fly."},
    {"id": "j255", "category": "school", "text": "The English teacher said: use beautiful in a sentence. The student said: my teacher is beautiful when she gives no homework."},
    {"id": "j256", "category": "school", "text": "Why did the history student bring a ladder? Because the stakes were high in the past."},
    {"id": "j257", "category": "school", "text": "What do you call a school for young ghosts? Elementary school. Elementary, my dear."},
    {"id": "j258", "category": "school", "text": "Why did the teacher wear sunglasses? Because her students were so bright."},
    {"id": "j259", "category": "school", "text": "What is the king of the classroom? The ruler."},
    {"id": "j260", "category": "school", "text": "Why did the computer get bad grades? It kept surfing the web during class."},
]


# --- Phase 10: merged joke bank (jokes_extra.py; 740 more, 1000 total) ---
# JOKES_BASE stays pristine so tests can verify the extra bank independently.
JOKES_BASE = list(JOKES)
try:
    from .jokes_extra import JOKES_EXTRA
    _seen_ids = {j["id"] for j in JOKES}
    JOKES.extend(j for j in JOKES_EXTRA if j["id"] not in _seen_ids)
    del _seen_ids
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Indexes
# ---------------------------------------------------------------------------

CATEGORIES: list[str] = [
    "tech", "programming", "desi", "dad", "oneliners",
    "science", "food", "work", "animals", "school",
]

_BY_ID: dict[str, dict] = {j["id"]: j for j in JOKES}
_BY_CATEGORY: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
for _j in JOKES:
    _BY_CATEGORY.setdefault(_j["category"], []).append(_j)


def _joke_payload(joke: dict) -> dict:
    return {"id": joke["id"], "category": joke["category"], "joke": joke["text"]}


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class JokesProvider(Provider):
    """Dynamic provider for the ``joke`` namespace: an offline joke bank.

    Addressable tools: ``joke.random``, ``joke.<category>`` (10 categories),
    and ``joke.<id>`` (one per joke).  Handlers never raise.
    """

    namespace = "joke"

    def expand(self) -> int:
        """Exact addressable count: 1 random + 10 categories + one per joke."""
        return 1 + len(CATEGORIES) + len(JOKES)

    def resolve(self, name: str) -> Tool | None:
        """Build exactly ONE Tool for a full dotted name, or None."""
        if not isinstance(name, str) or not self._owns(name):
            return None
        local = self._local(name)

        if local == "random":
            def _random(args: dict) -> dict:
                try:
                    if not isinstance(args, dict):
                        return {"error": "args must be an object"}
                    category = args.get("category")
                    if category is None:
                        pool = JOKES
                    elif isinstance(category, str) and category in _BY_CATEGORY:
                        pool = _BY_CATEGORY[category]
                    else:
                        return {"error": f"unknown category: {category!r}"}
                    return _joke_payload(random.choice(pool))
                except Exception as e:  # never raise out of a tool handler
                    return {"error": f"{type(e).__name__}: {e}"}

            return Tool(
                name=name,
                description="Tell a random joke from the offline joke bank (optionally from one category).",
                schema={"category": "string?"},
                handler=_random,
                risk="low",
            )

        if local in _BY_CATEGORY:
            def _category(args: dict, _cat=local) -> dict:
                try:
                    return _joke_payload(random.choice(_BY_CATEGORY[_cat]))
                except Exception as e:  # never raise out of a tool handler
                    return {"error": f"{type(e).__name__}: {e}"}

            return Tool(
                name=name,
                description=f"Tell a random {local} joke from the offline joke bank.",
                schema={},
                handler=_category,
                risk="low",
            )

        if local in _BY_ID:
            joke = _BY_ID[local]

            def _exact(args: dict, _j=joke) -> dict:
                try:
                    return _joke_payload(_j)
                except Exception as e:  # never raise out of a tool handler
                    return {"error": f"{type(e).__name__}: {e}"}

            return Tool(
                name=name,
                description=f"Tell joke {local} ({joke['category']}) exactly.",
                schema={},
                handler=_exact,
                risk="low",
            )

        return None

    def sample_names(self, n: int = 5) -> list[str]:
        """Representative dotted tool names for this namespace."""
        samples = ["joke.random", "joke.tech", "joke.desi", "joke.dad", "joke.j001"]
        return samples[: max(0, n)]
