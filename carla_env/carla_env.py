import sys
import os
import signal
import stat
import subprocess
import time

import gym
import gym.spaces
import numpy as np

CARLA_PATH = os.path.join(os.path.dirname(__file__), "carla_dist")

def download_carla_data():
    import tarfile
    import urllib

    def reporthook(blocknum, blocksize, totalsize):
        readsofar = blocknum * blocksize
        if totalsize > 0:
            percent = readsofar * 1e2 / totalsize
            s = "\r%5.1f%% %*d / %d" % (
                percent, len(str(totalsize)), readsofar, totalsize)
            sys.stderr.write(s)
            if readsofar >= totalsize: # near the end
                sys.stderr.write("\n")
        else: # total size is unknown
            sys.stderr.write("read %d\n" % (readsofar,))

    url = 'https://github.com/m-smith/carla-env/releases/download/v0.1/carla_dist.tar.gz'

    file_tmp = urllib.request.urlretrieve(url, filename=None, reporthook=reporthook)[0]
    base_name = os.path.basename(url)

    file_name, file_extension = os.path.splitext(base_name)

    print("Extracting...This may also take some time...")
    tar = tarfile.open(file_tmp)
    tar.extractall(os.path.dirname(__file__))
    print("Done.")

if not os.path.exists(CARLA_PATH):
    print("Downloading CARLA Environment. This may take some time.")
    download_carla_data()

sys.path.append(os.path.join(CARLA_PATH, 'PythonClient'))
from carla.client import CarlaClient
from carla.sensor import Camera
from carla.settings import CarlaSettings
import carla.tcp



def array_from_measurements(measurements):
    pm = measurements.player_measurements
    npm = [np.concatenate([
        array_from_loc(agent.vehicle.transform.location),
        [agent.vehicle.transform.rotation.yaw],
        [agent.vehicle.forward_speed]
    ]) for agent in measurements.non_player_agents if agent.HasField('vehicle')]
    return np.concatenate((
        array_from_loc(pm.transform.location),
        [pm.transform.rotation.yaw],
        array_from_loc(pm.acceleration),
        [
            pm.forward_speed,
            pm.collision_vehicles,
            pm.collision_other,
            pm.intersection_otherlane,
            pm.intersection_offroad
        ],
        *npm 
    ))

def array_from_loc(loc):
    return np.array([loc.x, loc.y])

def list_from_scalar(scalar):
    if np.isscalar(scalar):
        scalar = [scalar]
    return scalar

def get_open_port():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    s.listen(1)
    port = s.getsockname()[1]
    s.close()
    return port

class CarlaEnv(gym.Env):
    def __init__(
            self,
            host="localhost",
            num_vehicles=1,
            vehicles_seed=lambda: 200,
            player_starts=2,
            goals=0
        ):
        self.num_vehicles = num_vehicles
        self.vehicles_seed = vehicles_seed

        self.starts = list_from_scalar(player_starts)
        self.goals = list_from_scalar(goals)

        self.port = get_open_port()
        self.host = host
        self.metadata = {
            'render.modes': ['rgb_array'],
            #'video.frames_per_second': int(np.round(1.0 / self.dt))
        }

        self.server = self.open_server()
        print(f"Connecting to CARLA Client on  port {self.port}")
        self.client = CarlaClient(self.host, self.port, timeout=99999999)
        time.sleep(3)
        self.client.connect(connection_attempts=1000)
        print(f"Connected on port: {self.port}")
        
        self.action_space = gym.spaces.Box(-2, 2,
            (len(self._map_controls([1,2,3,4,5]).items()),),
            dtype=np.float32
        )

        self.height = 512
        self.width = 512

        obs_size = len(self.reset())
        #self.observation_space = gym.spaces.Box(-float("inf"), float("inf"), (obs_size,),  dtype=np.float32)
        self.observation_space = gym.spaces.Box(low=0, high=255, shape=(self.height, self.width, 3), dtype=np.uint8)

    def dist_from_goal(self, measurements):
        x = array_from_loc(measurements.player_measurements.transform.location)
        y = array_from_loc(self.scene.player_start_spots[self.goal].location)
        return np.sqrt(np.sum((x - y) ** 2))


    def open_server(self):
        #os.devnull
        log_file = f"./logs/carla_logs/carla_{self.port}.txt"
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        with open(log_file, "wb+") as out:
            """
            true_script_name = os.path.join(CARLA_PATH, 'CarlaUE4.sh')
            if os.path.islink(true_script_name):
                true_script_name = os.readlink(execute_path)
            project_root = os.path.dirname(true_script_name)
            execute_path = f"{project_root}/CarlaUE4/Binaries/Linux/CarlaUE4"
            
            # chmod +x
            st = os.stat(execute_path)
            os.chmod(execute_path, st.st_mode | stat.S_IEXEC)
            """
            cmd = [os.path.join(CARLA_PATH, 'CarlaUE4.sh'), #execute_path, "CarlaUE4",
                                "-carla-server", "-fps=60",
                                f"-world-port={self.port}",
                                f"-windowed -ResX={500} -ResY={500}",
                                "-carla-no-hud"]
            # - benchmark
            p = subprocess.Popen(cmd, stdout=out, stderr=out, stdin=subprocess.PIPE, preexec_fn=os.setpgrp)

        return p

    def close_server(self):
        #self.server.terminate()
        pid = self.server.pid
        no_of_attempts = 0
        def is_process_alive(pid):
            ## Source: https://stackoverflow.com/questions/568271/how-to-check-if-there-exists-a-process-with-a-given-pid-in-python
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            return True

        while is_process_alive(pid):
            pgroup = os.getpgid(self.server.pid)
            self.server.terminate()
            os.killpg(pgroup, signal.SIGTERM)
            _,_ = os.waitpid(pid, os.WNOHANG) 
            time.sleep(5)


    def _add_settings(self):

        self.settings.set(
            SynchronousMode=True,
            SendNonPlayerAgentsInfo=True,
            NumberOfVehicles=self.num_vehicles,
            NumberOfPedestrians=0,
            WeatherId=1,
            QualityLevel="Low",
            SeedVehicles=self.vehicles_seed()
        )
        #settings.randomize_seeds()

    def _add_sensors(self):
        camera0 = Camera('RenderCamera0')
        # Set image resolution in pixels.
        # camera0.set_image_size(800, 600)
        camera0.set_image_size(self.height, self.width)
        # Set its position relative to the car in meters.
        camera0.set_position(-4.30, 0, 2.60)
        camera0.set_rotation(pitch=-25, yaw=0, roll=0)

        self.settings.add_sensor(camera0)

    def _map_controls(self, a):
        return dict(
            steer=a[0],
            throttle=a[1]
        )

    def _process_observation(self, measurements, sensor_data):
        return array_from_measurements(measurements)

    def _get_reward_and_termination(self):
        measurements, sensor_data = self.current_state

        collision_penalty = (
            measurements.player_measurements.collision_vehicles + 
            measurements.player_measurements.collision_other
        )
        is_collided = collision_penalty > 2

        offroad_penalty = measurements.player_measurements.intersection_offroad
        is_offroad = offroad_penalty > 0.5

        dist_from_goal = self.dist_from_goal(measurements)

        is_at_target = dist_from_goal < 5

        is_done =  is_collided or is_at_target or is_offroad

        distance_reward = 300 - dist_from_goal
        collision_cost = collision_penalty / 300
        offroad_cost = (offroad_penalty * 10)
        reward = distance_reward - collision_cost - offroad_cost
        reward = reward / 20

        return reward, is_done


    def get_new_start_goal(self):

        start = np.random.choice(self.starts)
        goal = np.random.choice(self.goals)
        while goal == start:
            start = np.random.choice(self.starts)
            goal = np.random.choice(self.goals)

        return start, goal

    def reset(self):
        self.settings = CarlaSettings()
        self._add_settings()
        self._add_sensors()

        self.scene = self.client.load_settings(self.settings)

        start, goal = self.get_new_start_goal()

        self.goal = goal

        for i in range(100):
            try:
                self.client.start_episode(start)
                self.current_state = self.client.read_data()
                if i > 0:
                    print("Reconnected.")
                break
            except carla.tcp.TCPConnectionError:
                if i % 10 == 0:
                    print(f"There was a TCP Error (Attempt {i}). Retrying. ")
                time.sleep(3)

        measurements, sensor_data = self.current_state
        #return self._process_observation(measurements, sensor_data)

        return self.render(mode='rgb_array')

    def step(self, a):
        control = self._map_controls(a)
        for i in range(100):
            try:
                self.client.send_control(**control)
                break
            except carla.tcp.TCPConnectionError:
                time.sleep(0.5)

        self.current_state = self.client.read_data()
        measurements, sensor_data = self.current_state
        
        reward, is_done = self._get_reward_and_termination()
        #obs = self._process_observation(measurements, sensor_data)
        obs = self.render(mode='rgb_array')

        return obs, reward, is_done, {}


    def render(self, mode='human', **kwargs):
        if mode == 'rgb_array':
            return np.concatenate([sensor.data for name, sensor in self.current_state[1].items() if "Render" in name], axis=1)
        super().render(mode=mode, **kwargs)

    def _close(self):
        self.close()

    def close(self):
        print(f"Disconnecting from CARLA Client (port: {self.port}, pid: {self.server.pid})")
        self.close_server()
        time.sleep(3)
        while self.client.connected():
            self.client.disconnect()
        print(f"Disconnected from CARLA Client (port: {self.port}, pid: {self.server.pid}).")


if __name__ == "__main__":
    env = gym.wrappers.Monitor(CarlaEnv(), "./env_logs", force=True)
    try:
        env.reset()
        for i in range(1000):
            m, r, done, _ = env.step([0, 1, 0])
            if done:
                break
        for i in range(3):
            env.reset()
            for i in range(1000):
                a = np.random.uniform(-1,1) if i % 10 == 0 else a
                m, r, done, _ = env.step([a, np.random.uniform(0.5, 1), 0])
                print(r)
                if done:
                    break
    finally:
        env.unwrapped.close()
