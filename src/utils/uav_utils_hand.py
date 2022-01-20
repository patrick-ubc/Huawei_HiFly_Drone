from djitellopy import Tello
from curtsies import Input
from queue import Queue

def connect_uav():
    print("\n################################################################################")
    print("Connecting to Tello UAV...")
    try:
        uav = Tello()
        uav.connect()
        print("UAV connected successfully!")
        print(f"Current battery percentage: {uav.get_battery()}")
        return uav
    except Exception as e:
        print("Failed to connect to Tello UAV, please try to reconnect")
        raise 

def autoflight(uav):
    uav.takeoff()
    uav.rotate_counter_clockwise(180)
    uav.move_forward(50)
    uav.rotate_clockwise(90)
    uav.move_forward(40)
    uav.rotate_clockwise(90)
    uav.move_forward(40)
    uav.rotate_counter_clockwise(270)
    uav.move_forward(50)
    uav.land()

def manual_control(uav, q):
    print("---------------------------------------\n From control thread.....")
    print("Listen Key: \n \
          t: takeoff \n \
          l: land \n \
          w a s d: move forward/left/back/right \n \
          q e: rotate \n \
          Arrow Up/Down: move up/down")

    while True:
        val = q.get()
        if val == '3':
            uav.takeoff()
        elif val == '2':
            uav.land()  
